# Inference

We demonstrate how to run inference (next token prediction) with the Parrot base model in the [`generate.py`](generate.py) script:

```bash
python generate/base.py --prompt "Hello, my name is" --checkpoint_dir checkpoints/stabilityai/stablelm-base-alpha-3b
```
Output:
```
Hello, my name is Levi Durrer, I'm an Austrian journalist - Chairman of the Press Blair Party, with 37 years in the Press Blair International, and two years in the Spectre of Austerity for the other. I'm crossing my fingers that you will feel
```

The script assumes you have downloaded and converted the weights as described [here](download_stablelm.md).

This will run the 3B pre-trained model and require ~7 GB of GPU memory using the `bfloat16` datatype.

## Run interactively

You can also chat with the model interactively:

```bash
python chat/base.py --checkpoint_dir checkpoints/stabilityai/stablelm-tuned-alpha-3b
```

This script can work with any checkpoint. For the best chat-like experience, we recommend using it with a checkpoints
fine-tuned for chatting such as `stabilityai/stablelm-tuned-alpha-3b` or `togethercomputer/RedPajama-INCITE-Chat-3B-v1`.

## Run large models on consumer devices

On GPUs with `bfloat16` support, the `generate.py` script will automatically convert the weights and consume less memory.
For large models, GPUs with less memory, or ones that don't support `bfloat16`, enable quantization (`--quantize llm.int8`):

```bash
python generate.py --quantize llm.int8 --prompt "Hello, my name is"
```

For instance, `falcon-7b` requires ~15 GB without int8 and ~10GB with it. However, inference speed goes from 30 tokens/sec to 10 tokens/sec on an A100.
See `python generate.py --help` for more options.

You can also use GPTQ-style int4 quantization, but this needs conversions of the weights first:

```bash
python quantize/gptq.py --dtype bfloat16
```

GPTQ-style int4 quantization brings GPU usage down. As only the weights of the Linear layers are quantized, it is useful to also use `--dtype bfloat16` (default) even with the quantization enabled.

With the generated quantized checkpoint generation quantization then works as usual with `--quantize gptq.int4` and the newly generated checkpoint file:

```bash
python generate.py --quantize gptq.int4
```

For instance, `falcon-40b` "only" requires ~24 GB to generate using this technique, otherwise more than +40GB are required. On an A100 it takes 1 token/sec.
However, ~32 GB were required during the conversion step.


## Run multiple times after warming model up

```python
# Load the model, you can use quantization & int8, check the docstrings
model, tokenizer = generate.warm_then_infere.load_model()
# Infere
result_string = generate.warm_then_infere.infere("The secret to happiness is",max_new_tokens= 400, temperature= 0.2)
```