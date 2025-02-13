# Changelog

All notable changes to this project will be documented in this file.
Please follow [the Keep a Changelog standard](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

* Added support for more field attributes in `schema.had()` and `schema.didnt_have()`: `field_title_generator`, `fail_fast`, `coerce_numbers_to_str`, `union_mode`, `allow_mutation`, `pattern`, `discriminator`
* Added support for forwardrefs in body fields (for example, when you use `from __future__ import annotations` in the file with your routes)

## [4.5.0]

### Added

* `check_usage` argument to request/response by schema converters. Cadwyn always checks whether a schema mentioned in a converter applies to one or more endpoints to guarantee that the converter will apply to at least one endpoint. Sometimes, however, you do not need this validation. For example, when you use these converters for converting webhook bodies. Setting `check_usage=False` makes it possible to skip the validation

## [4.4.5]

### Fixed

* Fix invalid migration of pydantic v1 style root validators when pydantic erases information about their "skip_on_failure" attribute

## [4.4.4]

### Fixed

* Type hints for newest pydantic versions

## [4.4.3]

### Changed

* Bumped package versions

## [4.4.2]

### Fixed

* Non-function (object instances) dependencies not supporting dependency overrides in testing

## [4.4.1]

### Added

* Python 3.13 to CI

## [4.4.0]

### Added

* Support for [webhooks](https://fastapi.tiangolo.com/advanced/openapi-webhooks/) in swagger
* Automatic generation of versioned routes and webhooks upon the first request to Cadwyn. Notice that if you were using some of cadwyn's internal interfaces, this might break your code. If it did, make an issue and let's discuss your use case

## [4.3.1]

### Fixed

* Removed typer from main dependencies

## [4.3.0]

### Changed

* Migrated from poetry to uv (Contributed by @bastienpo)
* Removed CLI and Uvicorn dependencies from Cadwyn installations by default. Added a `standard` extras group to mirror FastAPI (Contributed by @bastienpo)

## [4.2.4]

### Fixed

* Background tasks not functioning in versioned endpoints

## [4.2.3]

### Fixed

* Added support for `embed_body_fields` in `solve_dependencies` and `create_model_field` in FastAPI. FastAPI has made a breaking change for these interfaces which is why we had to fix it
* Fixed invalid imports in quickstart docs
* Fixed default dependencies not including the CLI for FastAPI, thus causing the quickstart docs to be invalid

## [4.2.2]

### Fixed

* FastAPI Servers list did not include root path when mounted as a sub-app unless specified directly within the user defined server list (Contributed by @OD-tpeko)
* OpenAPI spec did not include the `summary` field (Contributed by @OD-tpeko)

## [4.2.1]

### Fixed

* Previously the docs were showing wrong versioned doc paths when cadwyn was mounted as a sub-app (Contributed by @OD-tpeko)

## [4.2.0]

### Added

* Automatic changelog generation from version changes using `Cadwyn.generate_changelog` method and `GET /changelog` endpoint.
* Automatic creation of versioned_routers based on the `VersionBundle` passed to `Cadwyn` which means that all versions mentioned in the version bundle will already be available for routing even without the use of `generate_and_include_versioned_routers`

## Changed

* Renamed `Version.version_changes` to `Version.changes`

### Removed

* `regex`, `include`, `min_items`, `max_items`, and `unique_items` arguments were removed from `schema(...).field(...).had`. Notice that it's not a breaking change for most cases because passing these arguments caused exceptions

## [4.1.0]

### Added

* Exposed `cadwyn.generate_versioned_models` that allow you to generate pydantic models and enums even without a FastAPI app

## [4.0.0]

Versions 3.x.x are still supported in terms of bug and security fixes but all the new features will go into versions 4.x.x.

### Added

* Runtime schema/enum generation
* Support for versions as ISO date strings instead of dates in `cadwyn.migrate_response_body` and `cadwyn.Version`

### Removed

* Pydantic 1 support
* Code generation from everywhere. It is now completely replaced by runtime generation (so schemas/enums are generated in the same manner as endpoints). This allows Cadwyn to version things outside of your project and allows you to pick any project structure unlike codegen that required a single "head" directory with all the versioned modules.
* CLI commands for codegen
* `cadwyn.main` because it is replaced by `cadwyn.__init__`
* `cadwyn.structure.module` as it was only necessary in codegen
* `cadwyn.VersionBundle.latest_schemas_package` and `cadwyn.VersionBundle.head_schemas_package`, `cadwyn.VersionBundle.versioned_modules`, `cadwyn.VersionBundle.versioned_directories_with_head`, `cadwyn.VersionBundle.versioned_directories_without_head`, because they were only necessary in code generation
* `cadwyn.Cadwyn.add_unversioned_routers` as you can now simply use FastAPI's `include_router`
* `cadwyn.Cadwyn.add_unversioned_routes` as you can now simply use any of FastAPI's methods for adding routes directly to the app
* `cadwyn.Cadwyn.enrich_swagger` as its functionality has been automated
* `cadwyn.InternalRepresentationOf` as it was deprecated previously and is now replaced with HeadVersion migrations

### Changed

* `VersionBundle.migrate_response_body` is no longer a method of `VersionBundle` and is now importable directly from `cadwyn` as a function
* `cadwyn.structure` is no longer recommended to be used directly because everything from it is now available in `cadwyn` directly

## [3.15.8]

### Fixed

* Invalid unparseable JSON response without quotes when the response was a raw string JSONResponse
* An exception raised during codegen if a pydantic model or its parent were created within some indent such as classes defined under if statements

## [3.15.7]

### Fixed

* Wrong globals being used for wrapped endpoints in older versions, sometimes causing FastAPI to fail to resolve forward references on endpoint generation (see #192 for more details)
* dependency_overrides not working for old versions of the endpoints because they were wrapped and wraps did not have the same `__hash__` and `__eq__` as the original dependencies

## [3.15.6]

### Added

* `HeadVersion` and `Version` into `cadwyn.__init__` so now they are directly importable from `cadwyn`

### Fixed

* Fix dependencies from other libraries not resolving if they use fastapi.Request or fastapi.Response (added svcs-specific test)
* Now a proper exception is used when no dated version was passed into `VersionBundle`

## [3.15.5]

### Fixed

* Fix dependency overrides not working for versioned routes

## [3.15.4]

### Changed

* `Cadwyn.enrich_swagger` is now completely unnecessary: openapi is now generated at runtime. It also now does not do anything, is deprecated, and will be removed in a future version

### Fixed

* fastapi-pagination now does not require an explicit call to `Cadwyn.enrich_swagger`

## [3.15.3]

### Changed

* `Cadwyn.router.routes` now includes all existing routers within the application instead of just unversioned routes

### Fixed

* Compatibility with fastapi-pagination
* High cardinality of metrics for routers with path variables in starlette-exporter

## [3.15.2]

### Fixed

* Openapi not being generated when lifespan was used

## [3.15.1]

### Fixed

* Badge links in the readme

## [3.15.0]

### Changed

* Optimized the calls to enrich_swagger which now happen on the startup event, once for the whole application

### Fixed

* Oauth2 authentication parameters did not get passed to swagger

## [3.14.0]

### Added

* Current API version to per-version openapi.json

## [3.13.0]

### Added

* Validation for path converters to make sure that impossible HTTP methods cannot be used
* Validation for both path and schema converters to make sure that they are used at some point. Otherwise, router generation will raise an error

## [3.12.1]

### Fixed

* `fastapi.Response` subclasses with non-null bodies and 500 response causing the response to not get returned
* `fastapi.Response` subclasses had invalid content length if migration affected it

## [3.12.0]

### Changed

* Rewritten header routing logic and structure to support the full feature set of FastAPI

## [3.11.1]

### Fixed

* Modules and enums from head versions not being detected and thus causing errors

## [3.11.0]

### Changed

* Header router is no longer reliant on the API version header -- now it simply takes the API version from the `VersionBundle.api_version_var`, thus making it easy for someone to extend header routing and set their own rules for how the default version is chosen

## [3.10.1]

### Fixed

* Previous version introduced a minor breaking change: if any old users depended on the pure `generate_versioned_routers` interface, their work would receive a minor yet simple breaking change.

## [3.10.0]

**Yanked** due to a minor breaking change that we fixed in 3.10.1.

### Added

* The new approach to internal schemas: instead of having them duplicate certain fields from `latest`, we introduced a new `HEAD` version -- the only one the user maintains by hand. All requests get migrated to `HEAD` and latest schemas are generated from `HEAD`. `cadwyn.structure.HeadVersion` was added to give us the ability to have migrations between `HEAD` and latest, thus eliminating the need for `InternalRepresentationOf` because all the used schemas are now the internal representations

### Changed

* `latest` is now named `head` because it no longer represents the newest version. Instead, it is the the `internally used` version and the version that is used for generating all other versions.
* the newest version is not aliased from `latest` anymore. Instead, it is generated like all the rest
* deprecated `InternalRepresentationOf` and the concept of `internal schemas` in favor of `HeadVersion` migrations

## [3.9.1]

### Fixed

* A broken link to docs in README.md

## [3.9.0]

### Added

* Support for getting openapi.json routes using API version headers instead of path query params

## [3.8.0]

### Added

* Discord status badge in README
* Logos to existing status badges in README
* An ability to specify multiple schemas when using `convert_request_to_next_version_for` and `convert_response_to_next_version_for` to be able to migrate multiple types of schemas using the same converter
* Redoc support

### Removed

* Dependency from verselect. Now it is included as a part of Cadwyn

### Fixed

* `h11._util.LocalProtocolError` when raising `HTTPException(status_code=500)`

## [3.7.1]

### Fixed

* Error message for changing path params of an endpoint in an incompatible manner which listed methods instead of path params

### Changed

* Deprecated `cadwyn generate-code-for-versioned-packages` and added `cadwyn codegen` instead. It doesn't require `template_package` argument anymore and does not have the `ignore_coverage_for_latest_aliases` argument as we plan to remove this feature in the future. So it only requires `version_bundle`.

## [3.7.0]

### Changed

* Deprecated `cadwyn generate-code-for-versioned-packages` and added `cadwyn codegen` instead. It doesn't require `template_package` argument anymore and does not have the `ignore_coverage_for_latest_aliases` argument as we plan to remove this feature in the future. So it only requires `version_bundle`.

## [3.6.6]

### Fixed

* When a class-based dependency from **fastapi** was used (anything security related), FastAPI had hardcoded `isinstance` checks for it which it used to enrich swagger with functionality. But when the dependencies were wrapped into our function wrappers, these checks stopped passing, thus breaking this functionality in swagger. Now we ignore all dependencies that FastAPI creates. This also introduces a hard-to-solve bug: if fastapi's class-based security dependency was subclassed and then `__call__` was overridden with new dependencies that are versioned -- we will not migrate them from version to version. I hope this is an extremely rare use case though. In fact, such use case breaks Liskov Substitution Principle and doesn't make much sense because security classes already include `request` parameter which means that no extra dependencies or parameters are necessary.

## [3.6.5]

### Fixed

* When a class-based dependency was used, its dependant was incorrectly generated, causing all affected endpoints to completely stop functioning

## [3.6.4] <!-- Test release -->

## [3.6.3]

### Fixed

* A rare pydantic 2 bug that caused `BaseModel` annotations to be corrupted when new fields were added to the schema

## [3.6.2]

### Fixed

* Removed exception when creating `cadwyn.Cadwyn` without `latest_schemas_package` as it was a minor breaking change

## [3.6.0]

### Added

* Add `cadwyn.VersionBundle.migrate_response_body` that allows us to migrate response bodies outside of routing and FastAPI
* `latest_schemas_package` argument to `cadwyn.VersionBundle` to support the migration above

### Removed

### Changed

* We now raise a 5xx error (`cadwyn.exceptions.CadwynLatestRequestValidationError`) whenever a request migration caused our payload to be incompatible with latest request schemas
* Deprecated `cadwyn.main` and use `cadwyn.applications` instead
* Deprecated `latest_schemas_package` argument in `cadwyn.Cadwyn`

## [3.5.0]

### Fixed

* Previously, Cadwyn did not set the default status code for ResponseInfo

### Added

* HTTP status error handling in response converters using `convert_response_to_previous_version_for(...,  migrate_http_errors=True)`

## [3.4.4]

### Fixed

* Request and response converters were not applied when path params were present

## [3.4.3]

### Added

* `RouterPathParamsModifiedError` is now raised if `endpoint(...).had(path=...)` has different path params than the original route

## [3.4.2]

### Fixed

* Fix import aliases in nested `__init__.py` files generating incorrectly for latest version

## [3.4.1]

### Fixed

* If the endpoint specified a single non-pydantic (list/dict) body parameter, Cadwyn failed to serialize the body

## [3.4.0]

### Added

* `schema(...).validator(...).existed` and `schema(...).validator(...).didnt_exist` instructions for simplistic manipulation of validators
* Automatic deletion of validators when the fields they validate get deleted
* `schema(...).field(...).didnt_have` for unsetting field attributes
* Improved support for `typing.Annotated` in schemas
* Full preservation of original abstract syntax trees for all field values and annotations

### Fixed

* If the user wrote a wrong signature in a transformer decorated by `convert_request_to_next_version_for` or `convert_response_to_previous_version_for`, the text of the error suggested the wrong argument count and names

## [3.3.4]

### Fixed

* Added backwards compatibility for FastAPI < 0.106.0

## [3.3.3]

### Fixed

* Guaranteed that it is impossible to release cadwyn with the wrong pydantic dependency

## [3.3.2]

### Fixed

* Downgrade required version of verselect for backwards compatibility

## [3.3.1]

### Fixed

* Removed lazy migrations as they were producing incorrect results when there were no migrations but when there were schema changes
* Added compatibility with fastapi>=0.109.0

## [3.3.0]

### Fixed

* If a user used a FastAPI/Starlette `StreamingResponse` or `FileResponse`, we still tried to access its `body` attribute which caused an `AttributeError`

## [3.2.0]

### Added

* Sponsors section to README and docs, along with Monite as our main and only current sponsor ✨

## [3.1.3]

### Fixed

* Switched to `better-ast-comments` because `ast-comments` had no license listed on pypi (even though its actual license was MIT) which caused some dependency checking tools to report it as unlicensed

## [3.1.2]

### Changed

* Migrate from black to ruff-format

### Fixed

* A rare Pydantic 2 bug in internal body schema handling when it was applied too early, causing partially incomplete data to arrive to the handler

## [3.1.1]

### Fixed

* Previously we did not pass `dependency_overrides_provider`, `response_model_exclude_unset` `response_model_exclude_defaults`, and `response_model_exclude_none` to `fastapi` which could cause erroneous behaviour during serialization in rare cases.

## [3.1.0]

### Added

* `module(...).had(import_=...)` construct for adding imports in older versions
* Codegen plugin system that allows easily customizing code generation for any purpose. It also significantly simplifies the core code of code generation

## [3.0.2]

### Fixed

* If a user returned a FastAPI/Starlette `Response` with an empty body, we still tried to serialize it which caused an invalid response body

## [Unreleased]

## [3.0.0]

### Added

* Pydantic 2 support
* Expanded reference section to docs
* Contributor docs
* Expanded makefile commands

### Changed

* internal request representation is now done using an annotation
* `latest_schemas_module` was renamed to `latest_schemas_package` everywhere
* `api_version_var` in `VersionBundle` is now an optional argument instead of a required one

### Removed

* `cadwyn.internal_body_representation_of` because it is now done using an annotation

## [2.3.4]

### Fixed

* `schema(...).field(...).had(ge=...)` for union fields previously raised an `AttributeError` on code generation

## [2.3.3]

### Fixed

* Field ASTs not preserving the original structure when constrained fields were changed

### Added

* Support for synchronous endpoints

## [2.3.2]

### Fixed

* The bug where fields from parent schemas also appeared in child schemas

## [2.3.1]

### Changed

* Migrate from external verselect to stable verselect

## [2.3.0]

### Fixed

* Previously whenever we generated routes, we wrapped all endpoints and callbacks in decorators for every version which caused stacktraces to be unnecessarily huge. Now there is only one wrapper for all versions

### Added

* `cadwyn.Cadwyn` class similar to `fastapi.FastAPI` that provides header routing and encapsulates all information about versioned routes
* Migrated from `fastapi-header-routing` to `verselect`
* `cadwyn.routing.VERSION_HEADER_FORMAT` from `verselect.routing`

### Changed

* `*versions` argument in `cadwyn.VersionBundle` is now split into a required positional-only `latest_version` and `*other_versions` to make it possible to see an invalid versionless definition statically. Note that it is not a breaking change because the presence of at least one version was also implicitly required before and would produce a failure at runtime

### Removed

* `cadwyn.get_cadwyn_dependency` and `cadwyn.header` because it is fully replaced with `verselect`

## [2.2.0]

### Added

* Validation for the spelling of HTTP methods in `cadwyn.structure.endpoint`.

## [2.1.0]

### Added

* A prototype of AST-based code generation where we try to keep as much of the old field/annotation structure as possible

## [2.0.5]

### Fixed

* `UploadFile` and forms have previously caused exceptions on request migration

## [2.0.4]

### Fixed

* `ContextVar[datetime.date]` being incompatible with `VersionBundle`

## [2.0.3]

### Added

* A note into reference docs about the paths specification in CLI

### Fixed

* Custom body fields created by fastapi caused an exception. Now they are ignored

## [2.0.2]

### Added

* A link to openapi discussion on enum expansion into docs/recipes
* A link to intercom's API versioning article into docs/theory

## [2.0.1]

### Fixed

* `generate_versioned_routers` did not alter `APIRoute.dependant.call`, `APIRoute.response_field`, and `APIRoute.secure_cloned_response_field` before which caused these fields to represent latest version on all generated versions. However, this was only a bug if the routes were later added into the app/router by hand instead of using `inherit_routes` or `add_api_route`.

## [2.0.0]

### Changed

* `generate_versioned_routers` now accepts only one router instead of a sequence of routers to give us the ability to guarantee that the type of generated routers is the same as the type of the passed router.

## [1.4.0]

### Added

* Theory section to docs

## [1.3.0]

### Added

* Recipes documentation section
* `schema(...).field(...).had(name=...)` functionality to rename fields

### Changed

* Tutorial example structure in tests

## [1.2.0] - 2023-10-16

### Added

* `cadwyn.main._Cadwyn` experimental private class for automatically adding the header dependency and managing all objects related to versioning

### Removed

* `cadwyn.header_routing` which only had experimental private functions

### Fixed

* Route callbacks didn't get migrated to versions with their parent routes

## [1.1.0] - 2023-10-13

### Added

* `ignore_coverage_for_latest_aliases` argument to `generate_code_for_versioned_packages` which controls whether we add "a pragma: no cover" comment to the star imports in the generated version of the latest module. It is True by default.

## [1.0.3] - 2023-10-10

### Fixed

* Add back the approach where the first version being an alias to latest in codegen

## [1.0.2] - 2023-10-09

### Fixed

* Add current working dir to `sys.path` when running code generation through CLI
* Use `exclude_unset` when migrating the body of a request to make sure that users' `exclude_unset` logic gets preserved

## [1.0.1] - 2023-10-09

### Fixed

* Pass first argument in `typer.Argument` to prevent errors on older typer versions

## [1.0.0] - 2023-10-09

### Added

* Command-line interface capable of running code-generation and outputting version info
* Internal request schema which gives us all the functionality we could ever need to migrate request bodies forward without any complexity of the prior solution
* `_get_versioned_router` and experimental header routing with it (by @tikon93). Note that the interface for this feature will change in the future

### Changed

* Renamed `cadwyn.regenerate_dir_to_all_versions` to `cadwyn.generate_code_for_versioned_packages`
* Renamed `cadwyn.generate_all_router_versions` to `cadwyn.generate_versioned_routers`

### Removed

* `unions` directory and all logic around it (replaced by internal request schema)
* `FillablePrivateAttr` and all logic around it (replaced by internal request schema)
* `schema(...).property` constructor and all logic around it (replaced by internal request schema)
* Special-casing for code generation of package with latest version using star imports from latest (replaced by internal request schema)
