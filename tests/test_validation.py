# Tests for the pure form validation (validation.py). Because validate_contact_form takes a
# plain dict and returns a (cleaned, error) pair with no Flask involved, these exercise it
# directly — no test client, no fixtures — which is the whole point of splitting it out.
from validation import MAX_EMAIL, MAX_MESSAGE, MAX_NAME, validate_contact_form


def _valid():
    return {"name": "Ada Lovelace", "email": "ada@example.com", "message": "Hello there."}


def test_valid_submission_passes_and_is_cleaned():
    cleaned, err = validate_contact_form(_valid())
    assert err is None
    assert cleaned == {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "message": "Hello there.",
    }


def test_surrounding_whitespace_is_stripped():
    data = {"name": "  Ada  ", "email": "  ada@example.com  ", "message": "  hi  "}
    cleaned, err = validate_contact_form(data)
    assert err is None
    assert cleaned == {"name": "Ada", "email": "ada@example.com", "message": "hi"}


def test_email_is_normalized():
    # email-validator lowercases the domain; the local part is preserved.
    data = {**_valid(), "email": "Ada@EXAMPLE.COM"}
    cleaned, err = validate_contact_form(data)
    assert err is None
    assert cleaned is not None
    assert cleaned["email"] == "Ada@example.com"


def test_non_string_field_is_rejected():
    data = {**_valid(), "email": 12345}
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid email."


def test_missing_field_is_rejected():
    data = {"name": "Ada", "message": "hi"}  # no email
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid email."


def test_empty_name_after_strip_is_rejected():
    data = {**_valid(), "name": "   "}
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid name"


def test_overlong_name_is_rejected():
    data = {**_valid(), "name": "a" * (MAX_NAME + 1)}
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid name"


def test_overlong_email_is_rejected():
    # The length cap is checked before the format check, so a 255-character address is
    # rejected on length and never reaches email-validator.
    data = {**_valid(), "email": ("a" * (MAX_EMAIL - 6)) + "@ex.com"}
    assert len(data["email"]) == MAX_EMAIL + 1
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid email"


def test_overlong_message_is_rejected():
    data = {**_valid(), "message": "a" * (MAX_MESSAGE + 1)}
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid message"


def test_malformed_email_is_rejected():
    data = {**_valid(), "email": "not-an-email"}
    cleaned, err = validate_contact_form(data)
    assert cleaned is None
    assert err == "Invalid email"


def test_crlf_in_name_is_rejected_header_injection_guard():
    # CR or LF in the name would split the Subject header if it reached the mailer.
    for bad in ["Ada\r\nBcc: evil@example.com", "Ada\nEvil", "Ada\rEvil"]:
        cleaned, err = validate_contact_form({**_valid(), "name": bad})
        assert cleaned is None
        assert err == "Invalid name"
