from app.worker.executors import (
    _dismiss_claimant_login_if_possible,
    _has_anti_bot_challenge,
    _has_login_wall,
)


class _Locator:
    def __init__(self, *, count=0, visible=False, text=""):
        self._count = count
        self._visible = visible
        self._text = text
        self.clicked = False
        self.first = self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text

    def click(self):
        self.clicked = True


class _Page:
    def __init__(self, mapping, body_text=""):
        self.mapping = mapping
        self.body = _Locator(count=1, visible=True, text=body_text)
        self.waited = 0

    def locator(self, selector):
        if selector == "body":
            return self.body
        for marker, locator in self.mapping.items():
            if marker in selector:
                return locator
        return _Locator()

    def wait_for_timeout(self, milliseconds):
        self.waited = milliseconds


def test_hidden_captcha_markup_does_not_count_as_a_challenge():
    page = _Page({"recaptcha": _Locator(count=1, visible=False)}, body_text="Log In to Your Account")
    assert _has_anti_bot_challenge(page) is False


def test_visible_captcha_control_is_detected():
    page = _Page({"recaptcha": _Locator(count=1, visible=True)})
    assert _has_anti_bot_challenge(page) is True


def test_claimant_login_is_classified_and_public_cancel_is_used():
    username = _Locator(count=1, visible=True)
    password = _Locator(count=1, visible=True)
    cancel = _Locator(count=1, visible=True)
    page = _Page({"username": username, "password": password, "Cancel": cancel})

    assert _has_login_wall(page) is True
    assert _dismiss_claimant_login_if_possible(page) is True
    assert cancel.clicked is True
    assert page.waited == 2000
