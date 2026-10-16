#include <cstdio>
#include <cstring>

#include "harness.hpp"

int main(int argc, char** argv) {
    const char* filter = argc > 1 ? argv[1] : nullptr;
    int ran = 0, failed = 0;
    for (const auto& c : dv02::test::registry()) {
        if (filter && !std::strstr(c.name, filter)) continue;
        const int before = dv02::test::failures();
        c.fn();
        ++ran;
        const bool ok = dv02::test::failures() == before;
        if (!ok) ++failed;
        std::printf("%s %s\n", ok ? "ok  " : "FAIL", c.name);
    }
    std::printf("%d tests, %d failed\n", ran, failed);
    return failed ? 1 : 0;
}
