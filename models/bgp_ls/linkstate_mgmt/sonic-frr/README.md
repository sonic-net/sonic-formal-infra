# FRR BGP-LS Google Test Suite Patches

This directory contains a patch series for FRR that integrates a standalone Google Test suite targeting `bgpd`'s Link-State capabilities and TED updates. 

The patch provides a bridge to execute FRR's BGP daemon in memory without the need to spin up Docker containers. It includes methods to inject BGP-LS updates, assert against the internal RIB and TED state, and collect code coverage metrics.

---

## Overview of Patches

The entire patch series is implemented under `tests/gtest` within the FRR root project folder.


### Google Test Framework and `bgpd` In-Memory Bridge
- Integrates the Google Test environment (`BgpdEnvironment`) to initialize a minimal, single-threaded instance of `bgpd` with network listening (`BGP_OPT_NO_LISTEN`) and Zebra dependencies disabled.
- Adds custom C bridge helpers (`frr_bridge.c` / `frr_bridge.h`) to pass BGP-LS byte streams directly into FRR process functions.

### BGP-LS Data Modeling and C++ Translation
- Decouples test case generation by reading model state transitions from JSON files.
- Models state primitives using `std::variant` (`TedVar`, `RibVar`) and templated `BApiLinkStateUpdate<T>` handlers to seamlessly support both link and prefix data structures.
- Adds `nlohmann::json` serializers for type-safe parsing of complex link-state structures.

### C/C++ Interoperability and Compatibility Solution
- Replicates C-only FRR BGP-LS internal structures in a dedicated C++ namespace to prevent macro/keyword collisions (e.g., C++ `delete` keyword) and compilation error cascades.
- Resolves C11 type mismatches when compiling FRR's string buffer implementation alongside C++ files by adding standard `<stdbool.h>` includes.

### Safety, Bug Fixes and Coverage Expansion
- Implements validation guards for unspecified zero addresses/IDs to eliminate Use-After-Free errors during test runs.
- Refactors update message handlers to send explicit link/prefix deletion events rather than node removals. This unlocks direct test coverage for `bgp_ls_withdraw_link` and `bgp_ls_withdraw_prefix` in `bgpd/bgp_ls_ted.c`.

### Developer Tooling and Diagnostics
- Automation scripts to set up Clang, Valgrind, and `gcovr` inside Docker development environments.
- Execution script outputting timestamped leak analysis logs under `logs/`.
- Convenience scripts for tracking test coverage specifically targeting `bgpd/` and `lib/` modules.

---

## Patch Series Summary

Below is the chronological list of commits included in this patch range:

| Module | Commit Subject | Description |
| :--- | :--- | :--- |
| `tests/gtest` | `set up initial Google Test suite...` | Initial framework, JSON parser, CMake setup, devcontainer, and `bgpd` environment setup. |
| `tests/gtest` | `send parsed JSON test cases...` | Added initial message construction and streaming logic to `bgpd`. |
| `tests/gtest` | `add Valgrind run script` | Added Valgrind execution script logging memory reports under `logs/`. |
| `tests/gtest` | `duplicate FRR's string buffer...` | Resolved standard `bool` C/C++ header issues via custom `sbuf.h`. |
| `tests/gtest` | `refactor FRR bridge to minimum...` | Optimized `bgpd` runtime initialization to operate single-threaded. |
| `tests/gtest` | `refactor model test body into...` | Modularized test runner into conversion, message sending, and address validation routines. |
| `tests/gtest` | `fix UAF bug due to passing...` | Added checks for unspecified ISO sys IDs and IPv6 zero addresses to prevent UAFs. |
| `tests/gtest` | `implement RIB table NLRI assertions...` | Created `linkstate_data.h` to replicate BGP-LS primitives and assert RIB states. |
| `tests/gtest` | `add prefix data structures` | Implemented model structures and JSON parsing for link-state prefix reachability. |
| `tests/gtest` | `refactor test execution around...` | Adopted `std::variant` (`TedVar`/`RibVar`) and ADL serializers for test state. |
| `tests/gtest` | `refactor data structures with...` | Templated `BApiLinkStateUpdate` to handle arbitrary link-state payload types. |
| `tests/gtest` | `implement prefix verification` | Added helper functions to verify prefix existence in the TED and RIB. |
| `tests/gtest` | `update devcontainer configuration...` | Configured Docker environment scripts for Clang, Valgrind, and `gcovr` coverage reporting. |
| `tests/gtest` | `refactor link-state data struct...` | Refactored deletion logic to specifically target links/prefixes for `bgp_ls_ted.c` coverage. |

---

## Applying Patches to FRR

### Prerequisites

Ensure you have a local checkout of the FRR repo, Git, Docker, and either the Devcontainer extension  on the host machine.


### Applying Patches using Git

Run these `git` commands within your local FRR repo. It is highly recommended to apply these patches on a separate branch to avoid commits incoming from FRR's remote repo. 

To view a summary of the patch, run the following command.
```bash
cd /path/to/frr
git apply --stat /path/to/patches/*.patch
```

Before applying the patch, check if the patch is applicable to the current working tree.
```bash
git apply --check /path/to/patches/*.patch
```

Once any local conflicts are resolved, apply the patch series retaining git commit metadata using the command below.
```bash
git am < /path/to/patches/*.patch
```

See the official Git docs on [`git-apply`](https://git-scm.com/docs/git-apply) and [`git-am`](https://git-scm.com/docs/git-am) for more info on these commands.

---

## Building and Running the Test Suite

### Prerequisites

The host should have Docker and the [Dev Container CLI](https://github.com/devcontainers/cli) or an IDE with a Dev Container extension, such as VSCode, VS 2022 and later, and CLion.

This patch has been primarily tested using the [VSCode Dev Containers extension](https://code.visualstudio.com/docs/devcontainers/containers). The rest of this guide will assume you are using VSCode. Steps may be similar on other platforms; however, each IDE's Dev Container extension may be implemented differently. Please check your IDE's developer documentation for more info on Dev Container development.

### Building using VSCode's Dev Containers extension

1. Open the `tests/gtest` directory inside your local FRR repo in VSCode.

2. VSCode will display a pop-up saying, "Folder containers a Dev Container configuration file. Reopen folder to develop in a container." Select the "Reopen in Container" option on the prompt.

3. After Docker finishes building the FRR image, it will execute the series of commands specified in `.devcontainer/setup.sh`.

4. Once all configuration scripts are finished, it is highly recommend to install the recommended extensions described in `.vscode/extensions.json`. These extensions enhance the development experience, providing the ability to use a debugger within the Dev Container and invoke CMake commands via VSCode.

5. Build the Google Test executable by invoking the `CMake: Build` command via the command palette (`Ctrl + Shift + P` with default keyboard shortcuts) or by pressing `F7`.

   - `CMake: Clean Rebuild` and `CMake: Delete Build Directory, Reconfigure, and Build` are also helpful when artifacts from previous builds happen to cause error.
   - Alternatively, using only the command line, you can generate the build system for the project using `cmake --preset gcc-debug`, and then build the executable using `cmake --build --preset gcc-debug`.

The executable should be located at `bin/Debug/model_tests`. It can be executed via the command line, or by pressing `Shift + F5` (`CMake: Debug`) or `Ctrl + Shift + F5` (`CMake: Run Without Debugging`).

> [!NOTE]
> Once the executable is built, you are now able to run the bash scripts located in the top-level directory of the project.
> - `run_gcovr.sh` - collects coverage information from `.gcno` and `.gcda` files.
> - `run_gtest_log.sh` - outputs GTest reports to a JSON file under the `logs` directory.
> - `run_valgrind.sh` - performs a memory leak test and outputs the report to the `logs` directory.