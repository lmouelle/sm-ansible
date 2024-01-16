#!/usr/bin/env python

# (c) 2023, Bitwarden <hello@bitwarden.com>
# Licensed under the GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from ansible.errors import AnsibleError, AnsibleLookupError
from ansible.plugins.lookup import LookupBase

import os
import sys
from urllib.parse import urlparse
import uuid

try:
    from bitwarden_sdk import (
        BitwardenClient,
        DeviceType,
        client_settings_from_dict,
        SecretResponse,
    )
except ImportError as bitwarden_sdk:
    BW_SDK_IMPORT_ERROR = bitwarden_sdk
else:
    BW_SDK_IMPORT_ERROR = None

if BW_SDK_IMPORT_ERROR:
    raise AnsibleError(
        "The bitwarden_sm lookup plugin requires the following python modules: 'bitwarden_sdk'."
    )

try:
    # noinspection PyCompatibility
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display

    display = Display()

DOCUMENTATION = """
name: bitwarden_sm
author: Bitwarden <hello@bitwarden.com>
version_added: "0.0.1"
short_description: Lookup secrets from Bitwarden Secrets Manager
description:
  - This lookup returns a secret from Bitwarden Secrets Manager.
options:
  _terms:
    description: 'secret id to lookup'
    required: true
    ansible.builtin.field:
      description: 'field to return (default: value)'
      required: false
      default: value
    ansible.builtin.base_url:
      description: 'base url to use (default: https://vault.bitwarden.com)'
      required: false
      default: https://vault.bitwarden.com
    ansible.builtin.api_url:
      description: 'api url to use (default: https://vault.bitwarden.com/api)'
      required: false
      default: https://vault.bitwarden.com/api
    ansible.builtin.identity_url:
      description: 'identity url to use (default: https://vault.bitwarden.com/identity)'
      required: false
      default: https://vault.bitwarden.com/identity
"""

EXAMPLES = """
- name: Lookup a secret
  ansible.builtin.debug:
    msg: "{{ lookup('bitwarden_sm', 'cdc0a886-6ad6-4136-bfd4-b04f01149173') }}"
- name: Get the note value for a secret
  ansible.builtin.debug:
    msg: "{{ lookup('bitwarden_sm', 'cdc0a886-6ad6-4136-bfd4-b04f01149173', field='note') }}"
"""

RETURN = """
_list:
  description: Value of the secret
  type: list
  elements: str
"""

BITWARDEN_BASE_URL: str = "https://vault.bitwarden.com"
BITWARDEN_API_URL: str = "https://vault.bitwarden.com/api"
BITWARDEN_IDENTITY_URL: str = "https://vault.bitwarden.com/identity"


def is_url(url: str) -> bool:
    try:
        result: urlparse = urlparse(url)
        return all([result.scheme in ["https"], result.netloc])
    except ValueError:
        return False


def is_valid_field(field: str) -> bool:
    valid_fields = [
        "id",
        "organizationId",
        "projectId",
        "key",
        "value",
        "note",
        "creationDate",
        "revisionDate",
    ]
    return field in valid_fields


def validate_url(url: str, url_type: str):
    if not is_url(url):
        raise AnsibleError(
            f"Invalid {url_type} URL, '{url}'. Update this value to be a valid HTTPS URL"
        )


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs) -> list[str]:
        self.process_terms(terms, kwargs)
        base_url, api_url, identity_url = self.get_urls(kwargs)
        self.validate_urls(base_url, api_url, identity_url)
        access_token, secret_id, field = self.get_env_and_args(kwargs)
        self.validate_args(secret_id, field)
        return self.get_secret_data(
            access_token, secret_id, field, api_url, identity_url
        )

    @staticmethod
    def process_terms(terms, kwargs):
        if not terms:
            raise AnsibleError("No secret ID provided")

        for term in terms:
            if "=" in term:
                key, value = term.split("=")
                kwargs[key] = value
            else:
                kwargs["secret_id"] = term

    @staticmethod
    def get_urls(kwargs) -> tuple[str, str, str]:
        base_url: str = kwargs.get("base_url", BITWARDEN_BASE_URL).rstrip("/")
        if base_url != BITWARDEN_BASE_URL:
            api_url: str = f"{base_url}/api"
            identity_url: str = f"{base_url}/identity"
        else:
            api_url: str = kwargs.get("api_url", BITWARDEN_API_URL).rstrip("/")
            identity_url: str = kwargs.get(
                "identity_url", BITWARDEN_IDENTITY_URL
            ).rstrip("/")
        return base_url, api_url, identity_url

    @staticmethod
    def get_env_and_args(kwargs) -> tuple[str, str, str]:
        access_token: str = os.getenv("BWS_ACCESS_TOKEN")
        secret_id: str = kwargs.get("secret_id")
        field: str = kwargs.get("field", "value")
        return access_token, secret_id, field

    def validate_args(self, secret_id, field):
        self.validate_secret_id(secret_id)
        self.validate_field(field)

    @staticmethod
    def validate_urls(base_url, api_url, identity_url):
        display.v("Parsing Bitwarden environment URL")
        validate_url(base_url, "base")
        validate_url(api_url, "API")
        validate_url(identity_url, "Identity")

    @staticmethod
    def validate_secret_id(secret_id):
        display.v("Parsing secret ID")
        try:
            uuid.UUID(secret_id)
        except ValueError as e:
            raise AnsibleError("Invalid secret ID. The secret ID must be a UUID") from e

    @staticmethod
    def validate_field(field):
        display.v("Validating field argument")
        if not is_valid_field(field):
            raise AnsibleError(
                "Invalid field. Update this value to be one of the following: "
                "id, organizationId, projectId, key, value, note, creationDate, revisionDate"
            )

    @staticmethod
    def get_secret_data(access_token, secret_id, field, api_url, identity_url):
        display.v("Authenticating with Bitwarden")
        client: BitwardenClient = BitwardenClient(
            client_settings_from_dict(
                {
                    "apiUrl": api_url,
                    "deviceType": DeviceType.SDK,
                    "identityUrl": identity_url,
                    "userAgent": "bitwarden/sm-ansible",
                }
            )
        )

        try:
            client.access_token_login(access_token)
            secret: SecretResponse = client.secrets().get(secret_id)
            secret_data: str = secret.to_dict()["data"][field]
            return [secret_data]

        except Exception:
            raise AnsibleLookupError(
                "The secret provided could not be found. Please ensure that the service "
                "account has access to the secret UUID provided"
            )


if __name__ == "__main__":
    LookupModule().run(
        sys.argv[1:],
        None,
        field="value",
        base_url=BITWARDEN_BASE_URL,
        api_url=BITWARDEN_API_URL,
        identity_url=BITWARDEN_IDENTITY_URL,
    )