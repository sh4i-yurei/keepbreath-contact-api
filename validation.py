# ============================================================
# keepbreath.ing — contact API (validation.py)
# ============================================================
# Server-side validation for the contact-form POST. This is a pure function: it takes the
# submitted data and returns a (cleaned, error) pair, with no Flask, no app state, and no
# side effects. It lives in its own module so the checks read on their own and can be
# unit-tested directly, without standing up a web request.
from email_validator import EmailNotValidError, validate_email

# Length caps on the submitted fields — never trust the client.
MAX_NAME = 75
MAX_EMAIL = 254
MAX_MESSAGE = 1000


def validate_contact_form(data: dict) -> tuple[dict[str, str] | None, str | None]:
    cleaned = {}
    for field in ["name", "email", "message"]:
        value = data.get(field)
        if not isinstance(value, str):
            return None, f"Invalid {field}."
        cleaned[field] = value
    # strip whitespace
    cleaned = {k: v.strip() for k, v in cleaned.items()}

    # basic length checks
    if not cleaned["name"] or len(cleaned["name"]) > MAX_NAME:
        return None, "Invalid name"
    if not cleaned["email"] or len(cleaned["email"]) > MAX_EMAIL:
        return None, "Invalid email"
    if not cleaned["message"] or len(cleaned["message"]) > MAX_MESSAGE:
        return None, "Invalid message"

    # reject CR/LF in name — it lands in the Subject header (injection guard)
    if "\r" in cleaned["name"] or "\n" in cleaned["name"]:
        return None, "Invalid name"

    # email format + safety via the email-validator library
    try:
        result = validate_email(cleaned["email"], check_deliverability=False)
        cleaned["email"] = result.normalized
    except EmailNotValidError:
        return None, "Invalid email"

    return cleaned, None
