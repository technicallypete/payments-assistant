from payments_assistant.core.security import hash_password, hash_token, new_token, verify_password


def test_tokens_are_unique_and_urlsafe():
    tokens = {new_token(24) for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) == 32 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)


def test_hash_token_is_stable_sha256_hex():
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse")
    assert not verify_password(h, "wrong")


def test_verify_password_rejects_garbage_hash():
    assert not verify_password("not-a-hash", "x")
