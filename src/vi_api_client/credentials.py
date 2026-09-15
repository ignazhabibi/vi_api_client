"""Persistence for the OAuth credential document."""

import json
import os
import stat
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

from ._types import JsonValue
from .exceptions import ViAuthError, ViResponseError
from .validation import validate_json_value


class CredentialDocument:
    """Read and safely replace one OAuth credential JSON document."""

    def __init__(self, path: Path) -> None:
        """Initialize the document at its configured path.

        Args:
            path: Location of the credential JSON document.
        """
        self.path = path

    def read(self) -> dict[str, JsonValue]:
        """Return credential data, treating an absent document as empty.

        Raises:
            ViAuthError: If the document cannot be read, does not contain a JSON
                object, or contains malformed known credential fields.
        """
        try:
            with self.path.open(encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ViAuthError(
                f"Token file '{self.path}' contains invalid JSON and was not "
                "modified. Repair or remove the file before authenticating again."
            ) from error
        except OSError as error:
            raise ViAuthError(
                f"Could not read token file '{self.path}': {error}"
            ) from error

        try:
            data = validate_json_value(data, path="Credential document")
        except ViResponseError as error:
            raise ViAuthError(
                f"Token file '{self.path}' contains invalid JSON data"
            ) from error
        if not isinstance(data, dict):
            raise ViAuthError(
                f"Token file '{self.path}' must contain a JSON object and was not "
                "modified. Repair or remove the file before authenticating again."
            )
        _validate_credential_fields(data)
        return data

    def update(self, token_data: object) -> None:
        """Merge token data and atomically replace the credential document.

        Args:
            token_data: JSON credential fields that should replace matching saved
                fields.

        Raises:
            ViAuthError: If supplied or existing credential data is invalid, the
                parent directory is unavailable, or the document cannot be saved.
        """
        try:
            validated_token_data = validate_json_value(
                token_data, path="Credential update"
            )
        except ViResponseError as error:
            raise ViAuthError("Credential update contains invalid JSON data") from error
        if not isinstance(validated_token_data, dict):
            raise ViAuthError("Credential update must be a JSON object")

        current_data = self.read()
        current_data.update(validated_token_data)
        _validate_credential_fields(current_data)
        self._replace(current_data)

    def _replace(self, data: dict[str, JsonValue]) -> None:
        """Write a sibling temporary file before atomically replacing the target."""
        if not self.path.parent.is_dir():
            raise ViAuthError(
                f"Token file parent directory '{self.path.parent}' does not exist."
            )

        temporary_file: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_file = Path(file.name)
                json.dump(data, file, indent=2)

            os.chmod(temporary_file, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary_file, self.path)
        except OSError as error:
            if temporary_file is not None:
                temporary_file.unlink(missing_ok=True)
            raise ViAuthError(
                f"Could not save token file '{self.path}': {error}"
            ) from error


def _validate_credential_fields(data: dict[str, JsonValue]) -> None:
    """Validate known authentication fields while allowing other JSON fields."""
    for field_name in (
        "access_token",
        "refresh_token",
        "token_type",
        "client_id",
        "redirect_uri",
    ):
        value = data.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ViAuthError(f"Credential field {field_name} must be a string")
    for field_name in ("expires_in", "expires_at"):
        value = data.get(field_name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
        ):
            raise ViAuthError(f"Credential field {field_name} must be a finite number")
