"""Observable CPU buffer reuse for historical native contracts."""

class CPUBuffer:
    def __init__(self):
        self.data = bytearray(64)

    def get_size(self):
        return len(self.data)

    def get_shapes(self):
        return [(64,)]

    def get_dtypes(self):
        return ["uint8"]

class ReusingAllocator:
    def __init__(self):
        self.pool = []
        self.freed = []
        self.free_error = None

    def get_backend_type(self, obj):
        return "cpu"

    def allocate(self, layout, count):
        from lmcache.v1.distributed.error import L1Error
        objects = [self.pool.pop() if self.pool else CPUBuffer() for _ in range(count)]
        for obj in objects:
            obj.data[:] = b"N" * 64
        return L1Error.SUCCESS, objects

    def free(self, objects):
        for obj in objects:
            self.freed.append(obj)
            obj.data[:] = b"F" * 64
            self.pool.append(obj)
        if objects and self.free_error:
            raise self.free_error
