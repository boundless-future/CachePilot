"""Restrict integrity comparisons to whole chunks actually written by retrieve."""
from dataclasses import replace


def whole_loaded_chunks(op, *, block_tokens, chunk_tokens):
    if chunk_tokens <= 0 or block_tokens <= 0 or chunk_tokens % block_tokens:
        raise ValueError("Unsupported geometry")
    if (op.start % chunk_tokens or op.end % chunk_tokens or op.end <= op.start
            or len(op.block_ids) != 1
            or len(op.block_ids[0]) * block_tokens != op.end - op.start
            or not 0 <= op.skip_first_n_tokens <= op.end - op.start):
        raise ValueError("Unsupported retrieve range")
    skipped = (op.skip_first_n_tokens + chunk_tokens - 1) // chunk_tokens * chunk_tokens
    if op.start + skipped >= op.end:
        return None
    return replace(op, start=op.start + skipped,
                   block_ids=[list(op.block_ids[0][skipped // block_tokens:])],
                   skip_first_n_tokens=0)
