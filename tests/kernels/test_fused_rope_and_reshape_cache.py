import random
from itertools import accumulate, product
from typing import List, Optional

import pytest
import torch

from vllm import _custom_ops as ops
from vllm.model_executor.layers.rotary_embedding import get_rope

from .allclose_default import get_default_atol, get_default_rtol

NUM_TOKENS = [42]  # Arbitrary values for testing

# Arbitrary values for testing
# don't make it too large. e.g. [1024, 36000] will OOM
NUM_BLOCKS = [1024, 10000]
# We assume fp8 is always enabled for testing.
KV_CACHE_DTYPE = ["auto", "fp8"]

BLOCK_SIZES = [8, 16, 32]

DTYPES = [torch.half, torch.bfloat16, torch.float]
HEAD_SIZES = [64, 80, 96, 112, 128, 192, 256]
ROTARY_DIMS = [None, 32]  # None means rotary dim == head size
NUM_HEADS = [7, 17]  # Arbitrary values for testing
BATCH_SIZES = [1, 5]  # Arbitrary values for testing
SEQ_LENS = [11, 8192]  # Arbitrary values for testing
IS_NEOX_STYLE = [True, False]
SEEDS = [0]
CUDA_DEVICES = [
    f"cuda:{i}" for i in range(1 if torch.cuda.device_count() == 1 else 2)
]


@pytest.mark.parametrize("num_tokens", NUM_TOKENS)
@pytest.mark.parametrize("block_size", BLOCK_SIZES)
@pytest.mark.parametrize("num_blocks", NUM_BLOCKS)
@pytest.mark.parametrize("kv_cache_dtype", KV_CACHE_DTYPE)
@pytest.mark.parametrize("batch_size", BATCH_SIZES)
@pytest.mark.parametrize("seq_len", SEQ_LENS)
@pytest.mark.parametrize("num_heads", NUM_HEADS)
@pytest.mark.parametrize("head_size", HEAD_SIZES)
@pytest.mark.parametrize("rotary_dim", ROTARY_DIMS)
@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("is_neox_style", IS_NEOX_STYLE)
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("device", CUDA_DEVICES)
@torch.inference_mode()
def test_fused_rotary_embedding_and_reshape_cache(
    kv_cache_factory,
    num_tokens: int,
    block_size: int,
    num_blocks: int,
    kv_cache_dtype: str,
    batch_size: int,
    seq_len: int,
    num_heads: int,
    head_size: int,
    rotary_dim: Optional[int],
    dtype: torch.dtype,
    is_neox_style: bool,
    seed: int,
    device: str,
    max_position: int = 8192,
    base: int = 10000,
) -> None:
    
    if rotary_dim is None:
        rotary_dim = head_size

    torch.random.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
    torch.set_default_device(device)

    if rotary_dim is None:
        rotary_dim = head_size
    rope = get_rope(head_size, rotary_dim, max_position, base, 
                    is_neox_style, fused_with_kv_cache_op=True)
    rope = rope.to(dtype=dtype)

    # Create a random slot mapping.
    num_slots = block_size * num_blocks
    slot_mapping = random.sample(range(num_slots), num_tokens)
    slot_mapping = torch.tensor(slot_mapping, dtype=torch.long)

    # Create the KV caches.
    key_caches, value_caches = kv_cache_factory(num_blocks, block_size, 1,
                                                num_heads, head_size,
                                                kv_cache_dtype, dtype, seed,
                                                device)
    key_cache, value_cache = key_caches[0], value_caches[0]

    # Clone the KV caches.
    if kv_cache_dtype == "fp8":
        cloned_key_cache = torch.empty_like(key_cache, dtype=torch.float16)
        ops.convert_fp8(cloned_key_cache, key_cache)
        cloned_value_cache = torch.empty_like(value_cache, dtype=torch.float16)
        ops.convert_fp8(cloned_value_cache, value_cache)
    else:
        cloned_key_cache = key_cache.clone()
        cloned_value_cache = value_cache.clone()

    # Using default kv_scale
    kv_scale = 1.0

    #------------------Simulate------------------------------

    positions = torch.randint(0, max_position, (batch_size, seq_len))
    query = torch.randn(batch_size,
                        seq_len,
                        num_heads * head_size,
                        dtype=dtype)
    key = torch.randn_like(query)
    ref_query, ref_key = rope._forward(positions, query, key)

    # Call the reshape_and_cache kernel.
    value = torch.randn_like(query)
    ops.reshape_and_cache(ref_key, value, cloned_key_cache, 
                          cloned_value_cache, slot_mapping,
                          kv_cache_dtype, kv_scale)

    if kv_cache_dtype == "fp8":
        result_key_cache = torch.empty_like(
                cloned_key_cache, dtype=torch.float16)
        ops.convert_fp8(result_key_cache, cloned_key_cache)
        result_value_cache = torch.empty_like(
                cloned_value_cache, dtype=torch.float16)
        ops.convert_fp8(result_value_cache, cloned_value_cache)
    
    #----------------------Actual-Run------------------------

    rope.forward(
        query, key, value, key_cache, value_cache, kv_cache_dtype,
        slot_mapping, kv_scale, kv_scale)

    #----------------------Assert----------------------------

    if kv_cache_dtype == "fp8":
        assert torch.allclose(key_cache,
                              cloned_key_cache,
                              atol=0.001,
                              rtol=0.1)
        assert torch.allclose(value_cache,
                              cloned_value_cache,
                              atol=0.001,
                              rtol=0.1)
    else:
        assert torch.allclose(key_cache, cloned_key_cache)
        assert torch.allclose(value_cache, cloned_value_cache)