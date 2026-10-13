cmake_minimum_required(VERSION 3.20)
project(dv02_engine VERSION 0.1.0 LANGUAGES CXX)

# DV02 Fund engine. C++20; Python sees it through nanobind (python/).
# Plain C++ build and tests:
#   cmake -S engine -B engine/build -G Ninja && cmake --build engine/build && ctest --test-dir engine/build
# Python module (what the API and the daily runner import):
#   pip install ./engine

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(DV02_BUILD_TESTS "Build the C++ tests" ON)
option(DV02_BUILD_PYTHON "Build the Python module dv02_engine._core" OFF)
option(DV02_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(DV02_WERROR "Treat warnings as errors (CI)" OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_library(dv02_options INTERFACE)
if(MSVC)
  target_compile_options(dv02_options INTERFACE /W4 $<$<BOOL:${DV02_WERROR}>:/WX>)
else()
  target_compile_options(dv02_options INTERFACE
    -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion
    $<$<BOOL:${DV02_WERROR}>:-Werror>)
  if(DV02_SANITIZE)
    target_compile_options(dv02_options INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
    target_link_options(dv02_options INTERFACE -fsanitize=address,undefined)
  endif()
endif()

add_library(dv02 STATIC
  src/risk.cpp
  src/costs.cpp
  src/backtest.cpp
  src/backtest_input.cpp
  src/backtest_metrics.cpp
)
target_include_directories(dv02 PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(dv02 PRIVATE dv02_options)

if(DV02_BUILD_TESTS)
  enable_testing()
  add_executable(dv02_tests
    tests/main.cpp
    tests/test_risk.cpp
    tests/test_backtest.cpp
    tests/test_backtest_commission.cpp
    tests/test_backtest_input.cpp
    tests/test_backtest_metrics.cpp
    tests/test_costs.cpp
    tests/test_backtest_costs.cpp
  )
  target_link_libraries(dv02_tests PRIVATE dv02 dv02_options)
  add_test(NAME dv02_tests COMMAND dv02_tests)
endif()

if(DV02_BUILD_PYTHON)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(_core NB_STATIC
    python/module.cpp
    python/bind_risk.cpp
    python/bind_costs.cpp
    python/bind_backtest.cpp
  )
  target_link_libraries(_core PRIVATE dv02)
  install(TARGETS _core LIBRARY DESTINATION dv02_engine)
endif()
