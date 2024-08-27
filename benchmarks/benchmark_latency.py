"""Benchmark the latency of processing a single batch of requests."""
import argparse
import json
import time
import os
from pathlib import Path
from typing import List, Optional

import numpy as np
import torch
from tqdm import tqdm

from vllm import LLM, SamplingParams
from vllm.inputs import PromptStrictInputs
from vllm.model_executor.layers.quantization import QUANTIZATION_METHODS
from vllm.utils import rpd_profiler_context, torch_profiler_context 

def main(args: argparse.Namespace):
    print(args)

    # NOTE(woosuk): If the request cannot be processed in a single batch,
    # the engine will automatically process the request in multiple batches.
    llm = LLM(model=args.model,
              speculative_model=args.speculative_model,
              num_speculative_tokens=args.num_speculative_tokens,
              tokenizer=args.tokenizer,
              quantization=args.quantization,
              quantized_weights_path=args.quantized_weights_path,
              tensor_parallel_size=args.tensor_parallel_size,
              trust_remote_code=args.trust_remote_code,
              dtype=args.dtype,
              enforce_eager=args.enforce_eager,
              kv_cache_dtype=args.kv_cache_dtype,
              quantization_param_path=args.quantization_param_path,
              device=args.device,
              ray_workers_use_nsight=args.ray_workers_use_nsight,
              worker_use_ray=args.worker_use_ray,
              use_v2_block_manager=args.use_v2_block_manager,
              enable_chunked_prefill=args.enable_chunked_prefill,
              download_dir=args.download_dir,
              block_size=args.block_size,
              disable_custom_all_reduce=args.disable_custom_all_reduce,
              gpu_memory_utilization=args.gpu_memory_utilization)

    sampling_params = SamplingParams(
        n=args.n,
        temperature=0.0 if args.use_beam_search else 1.0,
        top_p=1.0,
        use_beam_search=args.use_beam_search,
        ignore_eos=True,
        max_tokens=args.output_len,
    )
    print(sampling_params)
    dummy_prompt_token_ids = np.random.randint(10000,
                                               size=(args.batch_size,
                                                     args.input_len))
    dummy_inputs: List[PromptStrictInputs] = [{
        "prompt_token_ids": batch
    } for batch in dummy_prompt_token_ids.tolist()]
    
    def get_profiling_context(profile_dir: Optional[str] = None, trace_file_name = None):
         if args.profile_torch:
             return torch_profiler_context(profile_dir, trace_file_name)
         elif args.profile_rpd:
             return rpd_profiler_context(profile_dir, trace_file_name)
         else:
             return nullcontext()
    
    def run_to_completion(profile_dir: Optional[str] = None, profiling_mode = None):
        if profile_dir:
            name = os.path.basename(os.path.normpath(args.model))
            model_trace_name =f"{name}_in_{args.input_len}_out_{args.output_len}_batch_{args.batch_size}"
            with get_profiling_context(profile_dir, model_trace_name):
                llm.generate(dummy_inputs,
                             sampling_params=sampling_params,
                             use_tqdm=False)
        else:
            start_time = time.perf_counter()
            llm.generate(dummy_inputs,
                         sampling_params=sampling_params,
                         use_tqdm=False)
            end_time = time.perf_counter()
            latency = end_time - start_time
            return latency

    print("Warming up...")
    for _ in tqdm(range(args.num_iters_warmup), desc="Warmup iterations"):
        run_to_completion(profile_dir=None)
    
    if args.profile_torch or args.profile_rpd:
        profile_dir = args.profile_dir
        if not profile_dir:
            profile_dir = Path(".") / "vllm_benchmark_latency_result"
            os.makedirs(profile_dir, exist_ok=True)
        print(f"Profiling (results will be saved to '{profile_dir}')...")
        run_to_completion(profile_dir=profile_dir)    
        return

    # Benchmark.
    latencies = []
    for _ in tqdm(range(args.num_iters), desc="Profiling iterations"):
        latencies.append(run_to_completion(profile_dir=None))
    latencies = np.array(latencies)
    percentages = [10, 25, 50, 75, 90]
    percentiles = np.percentile(latencies, percentages)
    print(f'Avg latency: {np.mean(latencies)} seconds')
    for percentage, percentile in zip(percentages, percentiles):
        print(f'{percentage}% percentile latency: {percentile} seconds')

    # Output JSON results if specified
    if args.output_json:
        results = {
            "avg_latency": np.mean(latencies),
            "latencies": latencies.tolist(),
            "percentiles": dict(zip(percentages, percentiles.tolist())),
        }
        with open(args.output_json, "w") as f:
            json.dump(results, f, indent=4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Benchmark the latency of processing a single batch of '
        'requests till completion.')
    parser.add_argument('--model', type=str, default='facebook/opt-125m')
    parser.add_argument('--speculative-model', type=str, default=None)
    parser.add_argument('--num-speculative-tokens', type=int, default=None)
    parser.add_argument('--tokenizer', type=str, default=None)
    parser.add_argument('--quantization',
                        '-q',
                        choices=[*QUANTIZATION_METHODS, None],
                        default=None)
    parser.add_argument('--tensor-parallel-size', '-tp', type=int, default=1)
    parser.add_argument('--input-len', type=int, default=32)
    parser.add_argument('--output-len', type=int, default=128)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--n',
                        type=int,
                        default=1,
                        help='Number of generated sequences per prompt.')
    parser.add_argument('--use-beam-search', action='store_true')
    parser.add_argument('--num-iters-warmup',
                        type=int,
                        default=10,
                        help='Number of iterations to run for warmup.')
    parser.add_argument('--num-iters',
                        type=int,
                        default=30,
                        help='Number of iterations to run.')
    parser.add_argument('--trust-remote-code',
                        action='store_true',
                        help='trust remote code from huggingface')
    parser.add_argument(
        '--dtype',
        type=str,
        default='auto',
        choices=['auto', 'half', 'float16', 'bfloat16', 'float', 'float32'],
        help='data type for model weights and activations. '
        'The "auto" option will use FP16 precision '
        'for FP32 and FP16 models, and BF16 precision '
        'for BF16 models.')
    parser.add_argument('--enforce-eager',
                        action='store_true',
                        help='enforce eager mode and disable CUDA graph')
    parser.add_argument(
        '--kv-cache-dtype',
        type=str,
        choices=['auto', 'fp8', 'fp8_e5m2', 'fp8_e4m3'],
        default="auto",
        help='Data type for kv cache storage. If "auto", will use model '
        'data type. CUDA 11.8+ supports fp8 (=fp8_e4m3) and fp8_e5m2. '
        'ROCm (AMD GPU) supports fp8 (=fp8_e4m3)')
    parser.add_argument(
        '--quantization-param-path',
        type=str,
        default=None,
        help='Path to the JSON file containing the KV cache scaling factors. '
        'This should generally be supplied, when KV cache dtype is FP8. '
        'Otherwise, KV cache scaling factors default to 1.0, which may cause '
        'accuracy issues. FP8_E5M2 (without scaling) is only supported on '
        'cuda version greater than 11.8. On ROCm (AMD GPU), FP8_E4M3 is '
        'instead supported for common inference criteria.')
    parser.add_argument(
        '--quantized-weights-path',
        type=str,
        default=None,
        help='Path to the safetensor file containing the quantized weights '
        'and scaling factors. This should generally be supplied, when '
        'quantization is FP8.')
    parser.add_argument(
        '--profile_torch',
        action='store_true',
        help='profile the generation process of a single batch')
    parser.add_argument(
        '--profile_rpd',
        action='store_true',
        help='profile the generation process of a single batch')
    parser.add_argument(
        '--profile_dir',
        type=str,
        default=None,
        help=('path to save the profiler output. Can be visualized '
              'with ui.perfetto.dev or Tensorboard.'))
    parser.add_argument(
        "--device",
        type=str,
        default="cuda",
        choices=["cuda", "cpu"],
        help='device type for vLLM execution, supporting CUDA and CPU.')
    parser.add_argument('--block-size',
                        type=int,
                        default=16,
                        help='block size of key/value cache')
    parser.add_argument(
        '--enable-chunked-prefill',
        action='store_true',
        help='If True, the prefill requests can be chunked based on the '
        'max_num_batched_tokens')
    parser.add_argument('--use-v2-block-manager', action='store_true')
    parser.add_argument(
        "--ray-workers-use-nsight",
        action='store_true',
        help="If specified, use nsight to profile ray workers",
    )
    parser.add_argument('--worker-use-ray',
                        action='store_true',
                        help='use Ray for distributed serving, will be '
                        'automatically set when using more than 1 GPU '
                        'unless on ROCm where the default is torchrun')
    parser.add_argument('--download-dir',
                        type=str,
                        default=None,
                        help='directory to download and load the weights, '
                        'default to the default cache dir of huggingface')
    parser.add_argument(
        '--output-json',
        type=str,
        default=None,
        help='Path to save the latency results in JSON format.')
    parser.add_argument('--disable_custom_all_reduce', action='store_true')
    parser.add_argument('--gpu-memory-utilization',
                        type=float,
                        default=0.9,
                        help='the fraction of GPU memory to be used for '
                        'the model executor, which can range from 0 to 1.'
                        'If unspecified, will use the default value of 0.9.')
    args = parser.parse_args()
    main(args)
