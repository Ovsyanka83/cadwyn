# Add a field to openapi schemas

## To response schema

Let's say that we decided to expose the creation date of user's account with a `created_at` field in our API. This is **not** a breaking change so a new version is completely unnecessary. However, if you believe that you absolutely have to make a new version, then you can simply follow the recommended approach below but add a version change with [field didnt exist instruction](../../concepts/schema_migrations.md#remove-a-field-from-the-older-version).

The recommended approach:

1. Add `created_at` field into `data.latest.users.UserResource`
2. [Regenerate](../../concepts/code_generation.md) the versioned schemas

Now you have everything you need at your disposal: field `created_at` is available in all versions and your users do not even need to do any extra actions. Just make sure that the data for it is available in all versions too. If it's not: make the field optional.

## To both request and response schemas

### Field is optional

Let's say we want our users to be able to specify a middle name but it is nullable. It is not a breaking change so no new version is necessary whether it is requests or responses.

The recommended approach:

1. Add a nullable `middle_name` field into `data.latest.users.User`
2. [Regenerate](../../concepts/code_generation.md) the versioned schemas

### Field is required

#### With compatible default value in older versions

Let's say that our users had a field `country` that defaulted to `USA` but our product is now used well beyond United States so we want to make this field required in the `latest` version.

1. Remove `default="US"` from `data.latest.users.UserCreateRequest`
2. Add the following migration to `versions.v2001_01_01`:

    ```python
    from cadwyn.structure import (
        VersionChange,
        schema,
        convert_request_to_next_version_for,
    )
    from data.latest.users import UserCreateRequest, UserResource


    class MakeUserCountryRequired(VersionChange):
        description = 'Make user country required instead of the "USA" default'
        instructions_to_migrate_to_previous_version = (
            schema(UserCreateRequest).field("country").had(default="USA"),
        )

        @convert_request_to_next_version_for(UserCreateRequest)
        def add_time_field_to_request(request: RequestInfo):
            request.body["country"] = request.body.get("country", "USA")
    ```

3. [Regenerate](../../concepts/code_generation.md) the versioned schemas

That's it! Our old schemas will now contain a default but in `latest` country will be required. You might notice a weirdness: if we set a default in the old version, why would we also write a migration? That's because of a sad implementation detail of pydantic that [prevents us](../../concepts/schema_migrations.md#change-a-field-in-the-older-version) from using defaults from old versions.

#### With incompatible default value in older versions

Let's say that we want to add a required field `phone` to our users. However, older versions did not have such a field at all. This means that the field is going to be nullable in the old versions but required in the latest version. This also means that older versions contain a wider type (`str | None`) than the latest version (`str`). So when we try to migrate request bodies from the older versions to latest -- we might receive a `ValidationError` because `None` is not an acceptable value for `phone` field in the new version. Whenever we have a problem like this, when older version contains more data or a wider type set of data,  we use [internal body request schemas](../../concepts/version_changes.md#internal-request-body-representations).

1. Add `phone` field of type `str` to `data.latest.users.UserCreateRequest`
2. Add `phone` field of type `str | None` with a `default=None` to `data.latest.users.UserResource` because all users created with older versions of our API won't have phone numbers.
3. Add a `data.unversioned.users.UserInternalCreateRequest` that we will use later to wrap migrated data instead of the latest request schema. It will allow us to pass a `None` to `phone` from older versions while also guaranteeing that it is non-nullable in our latest version.

    ```python
    from pydantic import Field
    from ..latest.users import UserCreateRequest


    class UserInternalCreateRequest(UserCreateRequest):
        phone: str | None = Field(default=None)
    ```

4. Replace `UserCreateRequest` in your routes with `Annotated[UserInternalCreateRequest, InternalRepresentationOf[UserCreateRequest]]`:

    ```python
    from data.latest.users import UserCreateRequest, UserResource
    from cadwyn import InternalRepresentationOf
    from typing import Annotated


    @router.post("/users", response_model=UserResource)
    async def create_user(
        user: Annotated[
            UserInternalCreateRequest, InternalRepresentationOf[UserCreateRequest]
        ]
    ):
        ...
    ```

5. Add the following migration to `versions.v2001_01_01`:

    ```python
    from cadwyn.structure import (
        VersionChange,
        schema,
    )
    from data.latest.users import UserCreateRequest, UserResource


    class AddPhoneToUser(VersionChange):
        description = (
            "Add a required phone field to User to allow us to do 2fa and to "
            "make it possible to verify new user accounts using an sms."
        )
        instructions_to_migrate_to_previous_version = (
            schema(UserCreateRequest)
            .field("phone")
            .had(
                type=str | None,
                default=None,
            ),
        )
    ```

6. [Regenerate](../../concepts/code_generation.md) the versioned schemas

See how we didn't remove the `phone` field from old versions? Instead, we allowed a nullable `phone` field to be passed into both old `UserResource` and old `UserCreateRequest`. This gives our users new functionality without needing to update their API version! It is one of the best parts of Cadwyn's approach: our users can get years worth of updates without switching their API version and without their integration getting broken.
