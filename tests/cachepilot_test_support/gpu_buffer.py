"""Pinned-memory buffer double. Torch loads only when constructed."""


class PinnedBuffer:
    def __init__(self, size=64):
        import torch
        from types import SimpleNamespace
        self.tensor = torch.empty(size, dtype=torch.uint8, pin_memory=True)
        self.data = memoryview(self.tensor.numpy()).cast('B')
        self.meta = SimpleNamespace(address=0)

    @property
    def raw_tensor(self):
        return self.tensor

    @property
    def data_ptr(self):
        return self.tensor.data_ptr()

    def get_size(self):
        return self.tensor.numel()

    def get_shapes(self):
        return [tuple(self.tensor.shape)]

    def get_dtypes(self):
        return [self.tensor.dtype]
