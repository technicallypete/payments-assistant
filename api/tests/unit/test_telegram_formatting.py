import pytest

from payments_assistant.bot.formatting import split_message, to_telegram_html


@pytest.mark.parametrize(
    "md,html",
    [
        ("You owe **$180.00**.", "You owe <b>$180.00</b>."),
        ("__Paid__ in full", "<b>Paid</b> in full"),
        ("this is *really* due", "this is <i>really</i> due"),
        ("an _important_ note", "an <i>important</i> note"),
        ("ref `MAYA-0007`", "ref <code>MAYA-0007</code>"),
        (
            "[Pay here](https://invoice.stripe.com/i/abc)",
            '<a href="https://invoice.stripe.com/i/abc">Pay here</a>',
        ),
        ("- one\n- two", "• one\n• two"),
        ("* first item", "• first item"),
    ],
)
def test_conversions(md, html):
    assert to_telegram_html(md) == html


@pytest.mark.parametrize(
    "text",
    [
        "Invoice in_123_abc for $1,200.00 and $3,500.00",
        "snake_case_name stays",
        "2 * 3 * 4 = 24",
        "https://invoice.stripe.com/i/acct_1_x/test_abc_def?s=ap",
        "a_b and c_d",
    ],
)
def test_ids_amounts_and_urls_untouched(text):
    assert to_telegram_html(text) == text


def test_html_is_escaped():
    out = to_telegram_html("<script>alert('x')</script> & <b>fake</b>")
    assert "<script>" not in out and "<b>fake</b>" not in out
    assert "&lt;script&gt;" in out and "&amp;" in out


def test_non_http_links_are_not_linked():
    out = to_telegram_html("[click](javascript:alert(1))")
    assert "<a" not in out and "javascript:" in out


def test_code_contents_not_reinterpreted():
    assert to_telegram_html("`**not bold**`") == "<code>**not bold**</code>"


def test_link_text_keeps_url_underscores():
    out = to_telegram_html("[Pay](https://x.com/pay_now_ok)")
    assert out == '<a href="https://x.com/pay_now_ok">Pay</a>'


def test_split_short_message_unchanged():
    assert split_message("hello") == ["hello"]


def test_split_prefers_paragraph_boundaries():
    paras = ["a" * 40, "b" * 40, "c" * 40]
    chunks = split_message("\n\n".join(paras), limit=90)
    assert chunks == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]
    assert all(len(c) <= 90 for c in chunks)


def test_split_falls_back_to_lines_then_hard_split():
    text = "x" * 25 + "\n" + "y" * 25 + "\n" + "z" * 70
    chunks = split_message(text, limit=30)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")
