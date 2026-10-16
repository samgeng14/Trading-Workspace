// A tiny test harness: no third-party test framework to download or install.
//   TEST(name) { CHECK(cond); CHECK_NEAR(a, b, tol); }
// The runner (main.cpp) runs every test, or those whose name contains argv[1].
#pragma once

#include <cmath>
#include <cstdio>
#include <vector>

namespace dv02::test {

struct Case {
    const char* name;
    void (*fn)();
};

inline std::vector<Case>& registry() {
    static std::vector<Case> cases;
    return cases;
}

inline int& failures() {
    static int n = 0;
    return n;
}

struct Register {
    Register(const char* name, void (*fn)()) { registry().push_back({name, fn}); }
};

}  // namespace dv02::test

#define DV02_CAT_(a, b) a##b
#define DV02_CAT(a, b) DV02_CAT_(a, b)

#define TEST(name)                                                              \
    static void name();                                                         \
    static const ::dv02::test::Register DV02_CAT(register_, name)(#name, name); \
    static void name()

#define CHECK(cond)                                                                     \
    do {                                                                                \
        if (!(cond)) {                                                                  \
            ++::dv02::test::failures();                                                 \
            std::fprintf(stderr, "  %s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond); \
        }                                                                               \
    } while (0)

#define CHECK_NEAR(a, b, tol)                                                                             \
    do {                                                                                                  \
        const double dv02_a_ = (a), dv02_b_ = (b);                                                       \
        if (!(std::fabs(dv02_a_ - dv02_b_) <= (tol))) {                                                   \
            ++::dv02::test::failures();                                                                   \
            std::fprintf(stderr, "  %s:%d: CHECK_NEAR(%s, %s) failed: %.10g vs %.10g\n", __FILE__, __LINE__, \
                         #a, #b, dv02_a_, dv02_b_);                                                       \
        }                                                                                                 \
    } while (0)
