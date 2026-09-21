"""계좌 자격증명을 DB 에 **암호문으로** 두기 위한 최소 유틸.

왜 암호화하나 (2026-09-21):

운영 DB 는 공용 PostgreSQL(`global_shared_db`) 안에 있고, 그 인스턴스는
`postgres` 슈퍼유저 하나를 여러 프로젝트(tennis_cms·mio·nodeon…)가 공유한다.
KIS 앱키를 평문으로 두면 **그 비밀번호를 가진 무엇이든** 읽을 수 있고, DB 덤프와
백업에도 그대로 실린다. 키는 `.env`(root 전용 600)에만 두어, DB 를 통째로 떠가도
쓸 수 없게 한다.

키 생성(최초 1회):

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

→ `QLIB_API_SECRETS_KEY` 로 넣는다. **키를 잃어버리면 저장된 자격증명을 복구할 수
없다** — 그때는 계좌를 다시 등록하면 된다(KIS 포털에서 같은 값을 다시 볼 수 있다).

키가 없으면 **저장을 거부**한다. 조용히 평문으로 흘러드는 것이 최악이기 때문이다.
"""

from __future__ import annotations

from ..config import settings

# Fernet 인스턴스는 키가 바뀌지 않는 한 재사용한다. 테스트가 키를 갈아끼우므로
# 캐시를 비울 수 있어야 한다 — 그래서 lru_cache 가 아니라 명시적 모듈 변수다.
_cached_key: str | None = None
_cached_fernet = None


class SecretsKeyMissing(RuntimeError):
    """`QLIB_API_SECRETS_KEY` 가 없다 — 암호화할 수 없으니 저장하지 않는다."""


def available() -> bool:
    """키가 설정돼 있는가. 화면이 '계좌 추가'를 열어줄지 판단할 때 쓴다."""
    return bool((settings.secrets_key or "").strip())


def _fernet():
    global _cached_key, _cached_fernet
    key = (settings.secrets_key or "").strip()
    if not key:
        raise SecretsKeyMissing(
            "QLIB_API_SECRETS_KEY 가 비어 있다 — 자격증명을 평문으로 저장하지 않는다. "
            "`Fernet.generate_key()` 로 키를 만들어 .env 에 넣을 것.")
    if key != _cached_key or _cached_fernet is None:
        from cryptography.fernet import Fernet
        _cached_fernet = Fernet(key.encode())
        _cached_key = key
    return _cached_fernet


def encrypt(plain: str) -> str:
    """평문 → 암호문(문자열). 빈 값은 그대로 빈 값으로 둔다."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    """암호문 → 평문. 빈 값은 그대로.

    키가 바뀌었거나 값이 깨졌으면 `InvalidToken` 이 그대로 올라온다 — 조용히 빈
    문자열을 돌려주면 "자격증명이 없는 계좌"로 오인돼 그날 거래가 통째로 건너뛰어진다.
    시끄럽게 실패하는 쪽이 낫다.
    """
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def mask(plain: str, keep: int = 4) -> str:
    """화면·로그용. 앞뒤 몇 글자만 남긴다 — 어느 키인지 식별은 되되 복원은 불가."""
    if not plain:
        return ""
    if len(plain) <= keep * 2:
        return "…"
    return f"{plain[:keep]}…{plain[-keep:]}"
