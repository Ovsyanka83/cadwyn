# Changelog

All notable changes to this project will be documented in this file.
Please follow [the Keep a Changelog standard](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [1.3.0]

### Added

- Recipes documentation section
- `schema(...).field(...).had(name=...)` functionality to [rename fields](https://docs.cadwyn.dev/reference/#rename-a-schema)

### Changed

- Tutorial example structure in tests

## [1.2.0] - 2023-10-16

### Added

- `cadwyn.main._Cadwyn` experimental private class for automatically adding the header dependency and managing all objects related to versioning

### Removed

- `cadwyn.header_routing` which only had experimental private functions

### Fixed

- Route callbacks didn't get migrated to versions with their parent routes

## [1.1.0] - 2023-10-13

### Added

- `ignore_coverage_for_latest_aliases` argument to `generate_code_for_versioned_packages` which controls whether we add "a pragma: no cover" comment to the star imports in the generated version of the latest module. It is True by default.

## [1.0.3] - 2023-10-10

### Fixed

- Add back the approach where the first version being an alias to latest in codegen

## [1.0.2] - 2023-10-09

### Fixed

- Add current working dir to `sys.path` when running code generation through CLI
- Use `exclude_unset` when migrating the body of a request to make sure that users' `exclude_unset` logic gets preserved

## [1.0.1] - 2023-10-09

### Fixed

- Pass first argument in `typer.Argument` to prevent errors on older typer versions

## [1.0.0] - 2023-10-09

### Added

- Command-line interface capable of running code-generation and outputting version info
- Internal request schema which gives us all the functionality we could ever need to migrate request bodies forward without any complexity of the prior solution
- `_get_versioned_router` and experimental header routing with it (by @tikon93). Note that the interface for this feature will change in the future

### Changed

- Renamed `cadwyn.regenerate_dir_to_all_versions` to `cadwyn.generate_code_for_versioned_packages`
- Renamed `cadwyn.generate_all_router_versions` to `cadwyn.generate_versioned_routers`

### Removed

- `unions` directory and all logic around it (replaced by internal request schema)
- `FillablePrivateAttr` and all logic around it (replaced by internal request schema)
- `schema(...).property` constructor and all logic around it (replaced by internal request schema)
- Special-casing for code generation of package with latest version using star imports from latest (replaced by internal request schema)
