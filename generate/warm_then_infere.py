import json
import sys
import time
import warnings
from pathlib import Path
from typing import Optional, Tuple

import lightning as L
import torch

# support running without installing as a package
wd = Path(__file__).parent.parent.resolve()
sys.path.append(str(wd))

from lit_parrot import Parrot, Tokenizer, Config
from lit_parrot.utils import EmptyInitOnDevice, lazy_load, check_valid_checkpoint_dir

torch.set_float32_matmul_precision("high")
warnings.filterwarnings(
    # Triggered internally at ../aten/src/ATen/EmptyTensor.cpp:31
    "ignore",
    message="ComplexHalf support is experimental and many operators don't support it yet",
)
warnings.filterwarnings(
    # Triggered in bitsandbytes/autograd/_functions.py:298
    "ignore",
    message="MatMul8bitLt: inputs will be cast from torch.bfloat16 to float16 during quantization",
)


@torch.no_grad()
def generate(
        model: torch.nn.Module,
        idx: torch.Tensor,
        max_returned_tokens: int,
        max_seq_length: int,
        *,
        temperature: float = 1.0,
        top_k: Optional[int] = None,
        eos_id: Optional[int] = None,
) -> torch.Tensor:
    """Takes a conditioning sequence (prompt) as input and continues to generate as many tokens as requested.

    The implementation of this function is modified from A. Karpathy's nanoGPT.

    Args:
        model: The model to use.
        idx: Tensor of shape (T) with indices of the prompt sequence.
        max_returned_tokens: The maximum number of tokens to return (given plus generated).
        max_seq_length: The maximum sequence length allowed. Should be less or equal than the block size.
        temperature: Scales the predicted logits by 1 / temperature.
        top_k: If specified, only sample among the tokens with the k highest probabilities.
        eos_id: If specified, stop generating any more token once the <eos> token is triggered.
    """
    T = idx.size(0)
    assert max_returned_tokens > T
    device, dtype = idx.device, idx.dtype
    # create an empty tensor of the expected final shape and fill in the current tokens
    empty = torch.empty(max_returned_tokens, dtype=dtype, device=device)
    empty[:T] = idx
    idx = empty
    input_pos = torch.arange(0, T, device=device)

    if idx.device.type == "xla":
        import torch_xla.core.xla_model as xm

        xm.mark_step()

    # generate up to a fixed number of tokens
    for _ in range(max_returned_tokens - T):
        x = idx.index_select(0, input_pos).view(1, -1)

        # forward
        logits = model(x, max_seq_length, input_pos)
        logits = logits[0, -1] / temperature

        # optionally crop the logits to only the top k options
        if top_k is not None:
            v, _ = torch.topk(logits, min(top_k, logits.size(-1)))
            logits = torch.where(logits < v[[-1]], -float("Inf"), logits)

        probs = torch.nn.functional.softmax(logits, dim=-1)
        idx_next = torch.multinomial(probs, num_samples=1).to(dtype=dtype)

        # advance
        input_pos = input_pos[-1:] + 1

        if idx.device.type == "xla":
            xm.mark_step()

        # concatenate the new generation
        idx = idx.index_copy(0, input_pos, idx_next)

        # if <eos> token is triggered, return the output (stop generation)
        if idx_next == eos_id:
            return idx[:input_pos]  # include the EOS token

    return idx


def load_model(
        checkpoint_dir: Path = Path(f"checkpoints/stabilityai/stablelm-base-alpha-3b"),
        quantize: Optional[str] = None,
) -> Tuple[torch.nn.Module, Tokenizer]:
    """Generates text samples based on a pre-trained model and tokenizer.

    Args:
        prompt: The prompt string to use for generating the samples.
        num_samples: The number of text samples to generate.
        max_new_tokens: The number of generation steps to take.
        top_k: The number of top most probable tokens to consider in the sampling process.
        temperature: A value controlling the randomness of the sampling process. Higher values result in more random
            samples.
        checkpoint_dir: The checkpoint directory to load.
        quantize: Whether to quantize the model and using which method:
            ``"llm.int8"``: LLM.int8() mode,
            ``"gptq.int4"``: GPTQ 4-bit mode.
    """
    check_valid_checkpoint_dir(checkpoint_dir)

    with open(checkpoint_dir / "lit_config.json") as fp:
        config = Config(**json.load(fp))

    fabric = L.Fabric(devices=1)
    dtype = torch.bfloat16 if fabric.device.type == "cuda" and torch.cuda.is_bf16_supported() else torch.float32

    if quantize == "gptq.int4":
        model_file = "lit_model_gptq.4bit.pth"
        if not (checkpoint_dir / model_file).is_file():
            raise ValueError("Please run `python quantize/gptq.py` first")
    else:
        model_file = "lit_model.pth"
    checkpoint_path = checkpoint_dir / model_file
    print(f"Loading model {str(checkpoint_path)!r} with {config.__dict__}", file=sys.stderr)
    t0 = time.time()
    with EmptyInitOnDevice(device=fabric.device, dtype=dtype, quantization_mode=quantize):
        model = Parrot(config)
    with lazy_load(checkpoint_path) as checkpoint:
        model.load_state_dict(checkpoint, strict=False)
    print(f"Time to load model: {time.time() - t0:.02f} seconds.", file=sys.stderr)

    model.eval()
    model = fabric.setup_module(model)

    tokenizer = Tokenizer(checkpoint_dir / "tokenizer.json", checkpoint_dir / "tokenizer_config.json")

    return model, tokenizer


def infere(
        tokenizer: Tokenizer,
        model: torch.nn.Module,
        *,
        prompt: str = "Hello, my name is",
        num_samples: int = 1,
        max_new_tokens: int = 50,
        top_k: int = 20,
        temperature: float = 0.8,
) -> str:
    """
    Generates and returns text predictions based on a given input prompt using a specified tokenizer and model.

    Args:
        tokenizer (Tokenizer): The tokenizer to be used for encoding the input prompt and decoding the model's output.
        model: (torch.nn.Module): The pre-trained model to be used for generating the predictions.
        prompt (str, optional): The input text used as a basis for generating predictions. Defaults to "Hello, my name is".
        num_samples (int, optional): The number of predictions to generate. Defaults to 1.
        max_new_tokens (int, optional): The maximum number of new tokens to be generated by the model. Defaults to 50.
        top_k (int, optional): The number of top candidates to consider during token generation. Defaults to 20.
        temperature (float, optional): The parameter that controls the randomness of predictions. A higher temperature results in more random outputs. Defaults to 0.8.

    Returns:
        str: The generated text prediction. If num_samples > 1, it yields the result for each prediction one by one.

    Raises:
        AssertionError: If the maximum number of returned tokens exceeds the model's block size.

    Note:
        The function also provides diagnostic outputs about inference time and, if running on a CUDA device, memory usage.
    """
    encoded = tokenizer.encode(prompt, device=fabric.device)
    prompt_length = encoded.size(0)
    max_returned_tokens = prompt_length + max_new_tokens
    assert max_returned_tokens <= model.config.block_size, (
        max_returned_tokens,
        model.config.block_size,
    )  # maximum rope cache length

    L.seed_everything(1234)
    for i in range(num_samples):
        t0 = time.perf_counter()
        y = generate(
            model,
            encoded,
            max_returned_tokens,
            max_seq_length=max_returned_tokens,
            temperature=temperature,
            top_k=top_k,
        )
        t = time.perf_counter() - t0

        model.reset_cache()
        result_as_string = tokenizer.decode(y)
        tokens_generated = y.size(0) - prompt_length
        print(
            f"Time for inference {i + 1}: {t:.02f} sec total, {tokens_generated / t:.02f} tokens/sec", file=sys.stderr
        )

    if fabric.device.type == "cuda":
        print(f"Memory used: {torch.cuda.max_memory_reserved() / 1e9:.02f} GB", file=sys.stderr)

    if num_samples == 1:
        return result_as_string
    else:
        yield result_as_string
