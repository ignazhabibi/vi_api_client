"""Persistence for the OAuth credential document."""

import json
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .exceptions import ViAuthError


class CredentialDocument:
    """Read and safely replace one OAuth credential JSON document."""

    def __init__(self, path: Path) -> None:
        """Initialize the document at its configured path.

        Args:
            path: Location of the credential JSON document.
        """
        self.path = path

    def read(self) -> dict[str, Any]:
        """Return credential data, treating an absent document as empty.

        Raises:
            ViAuthError: If the document cannot be read or does not contain a JSON
                object.
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

        if not isinstance(data, dict):
            raise ViAuthError(
                f"Token file '{self.path}' must contain a JSON object and was not "
                "modified. Repair or remove the file before authenticating again."
            )
        return data

    def update(self, token_data: dict[str, Any]) -> None:
        """Merge token data and atomically replace the credential document.

        Args:
            token_data: Credential fields that should replace matching saved fields.

        Raises:
            ViAuthError: If the existing document cannot be read or validated, the
                parent directory is unavailable, or the document cannot be saved.
        """
        current_data = self.read()
        current_data.update(token_data)
        self._replace(current_data)

    def _replace(self, data: dict[str, Any]) -> None:
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
