// Experimental CachePilot ownership primitive; not installed into LMCache.
// Shared TTL refresh follows TTLLock semantics, but release requires identity.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <unordered_set>
#include <vector>

namespace py = pybind11;

struct Reservation {
    uint64_t lock_id, epoch, serial;
};

enum class ReleaseStatus { RELEASED, STALE_EPOCH, INACTIVE, FOREIGN_LOCK };

class ReservationLock {
    using Clock = std::chrono::steady_clock;
    inline static std::atomic<uint64_t> next_id{1};
    const uint64_t id;
    const std::chrono::milliseconds ttl;
    std::mutex mutex;
    uint64_t epoch = 1, serial = 0;
    Clock::time_point deadline{};
    std::unordered_set<uint64_t> live;

    static uint64_t allocate_id() {
        auto current = next_id.load();
        while (true) {
            if (current == std::numeric_limits<uint64_t>::max())
                throw std::overflow_error("Lock identity exhausted");
            if (next_id.compare_exchange_weak(current, current + 1)) return current;
        }
    }

    void advance_epoch() {
        if (epoch == std::numeric_limits<uint64_t>::max())
            throw std::overflow_error("Reservation epoch exhausted");
        ++epoch;
        live.clear();
    }

    void expire(Clock::time_point now) {
        if (!live.empty() && now >= deadline) advance_epoch();
    }

public:
    explicit ReservationLock(uint32_t ttl_ms) : id(allocate_id()), ttl(ttl_ms) {
        if (ttl_ms == 0) throw std::invalid_argument("TTL must be positive");
    }

    std::vector<Reservation> acquire(uint32_t count = 1) {
        if (count < 1 || count > 128)
            throw std::invalid_argument("Reader count must be in [1, 128]");
        std::lock_guard<std::mutex> guard(mutex);
        auto now = Clock::now();
        expire(now);
        if (serial > std::numeric_limits<uint64_t>::max() - count)
            throw std::overflow_error("Reservation serial exhausted");
        std::vector<Reservation> result;
        result.reserve(count);
        // Prepare both allocations before mutating the live ledger.
        auto updated = live;
        for (uint32_t i = 0; i < count; ++i) {
            const auto number = serial + i + 1;
            updated.insert(number);
            result.push_back({id, epoch, number});
        }
        live.swap(updated);
        serial += count;
        deadline = now + ttl;
        return result;
    }

    ReleaseStatus release(const Reservation& token) {
        std::lock_guard<std::mutex> guard(mutex);
        expire(Clock::now());
        if (token.lock_id != id) return ReleaseStatus::FOREIGN_LOCK;
        if (token.epoch != epoch) return ReleaseStatus::STALE_EPOCH;
        return live.erase(token.serial) ? ReleaseStatus::RELEASED : ReleaseStatus::INACTIVE;
    }

    uint64_t live_count() {
        std::lock_guard<std::mutex> guard(mutex);
        expire(Clock::now());
        return live.size();
    }

    bool is_locked() { return live_count() != 0; }

    void reset() {
        std::lock_guard<std::mutex> guard(mutex);
        advance_epoch();
    }
};

PYBIND11_MODULE(cachepilot_reservation_native, m) {
    m.doc() = "CPU-only reservation lock prototype; not an LMCache replacement";
    py::class_<Reservation>(m, "Reservation")
        .def_readonly("lock_id", &Reservation::lock_id)
        .def_readonly("epoch", &Reservation::epoch)
        .def_readonly("serial", &Reservation::serial);
    py::enum_<ReleaseStatus>(m, "ReleaseStatus")
        .value("RELEASED", ReleaseStatus::RELEASED)
        .value("STALE_EPOCH", ReleaseStatus::STALE_EPOCH)
        .value("INACTIVE", ReleaseStatus::INACTIVE)
        .value("FOREIGN_LOCK", ReleaseStatus::FOREIGN_LOCK);
    py::class_<ReservationLock>(m, "ReservationLock")
        .def(py::init<uint32_t>(), py::arg("ttl_ms"))
        .def("acquire", &ReservationLock::acquire, py::arg("count") = 1,
             py::call_guard<py::gil_scoped_release>())
        .def("release", &ReservationLock::release, py::call_guard<py::gil_scoped_release>())
        .def("is_locked", &ReservationLock::is_locked, py::call_guard<py::gil_scoped_release>())
        .def("live_count", &ReservationLock::live_count, py::call_guard<py::gil_scoped_release>())
        .def("reset", &ReservationLock::reset, py::call_guard<py::gil_scoped_release>())
        .def("lock", [](ReservationLock&) {
            throw std::runtime_error("Anonymous acquisition forbidden: use acquire() and retain tokens");
        })
        .def("unlock", [](ReservationLock&) {
            throw std::runtime_error("Anonymous release forbidden: supply a reservation token");
        });
}
