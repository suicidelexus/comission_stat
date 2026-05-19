from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hybrid_host: str
    hybrid_agency_label: str = "Hybrid"
    hybrid_cookies: str
    hybrid_user_id: str
    hybrid_timezone_id: int = 305

    # Список agency_id через запятую. Если пусто — берём ВСЕ доступные.
    hybrid_agency_ids: str = ""

    # Второй кабинет (опционально). Если HYBRID_HOST_2 пустой — игнорируется.
    hybrid_host_2: str = ""
    hybrid_agency_label_2: str = ""
    hybrid_cookies_2: str = ""
    hybrid_user_id_2: str = ""
    hybrid_agency_ids_2: str = ""

    pace_green_threshold: float = 1.0
    min_days_left_for_green: int = 3

    # Регэксп — если имя advertiser'а матчит, скипаем (явно мусорные имена).
    # balance НЕ фильтруем — баланс может быть переведён на сами кампании, и при
    # advertiser.balance=0 у него вполне могут быть активные кампании.
    skip_advertiser_name_regex: str = r"(?i)(for deleting|archive|^test\b|^удалить)"

    # Сколько параллельных GetTotal'ов внутри одного агентства.
    fetch_concurrency: int = 30

    # Сколько секунд между фоновыми sync'ами. 0 = выключено.
    background_sync_interval_seconds: int = 0

    # Автосинк по расписанию в формате HH:MM локального времени сервера.
    # Пусто или "off" = выключен. По умолчанию 03:00 (3 ночи).
    auto_sync_time: str = "03:00"

    @property
    def agency_ids(self) -> list[str]:
        return [x.strip() for x in self.hybrid_agency_ids.split(",") if x.strip()]

    @property
    def tenants(self) -> list["TenantConfig"]:
        out = [
            TenantConfig(
                label=self.hybrid_agency_label or self.hybrid_host,
                host=self.hybrid_host,
                cookies=self.hybrid_cookies,
                user_id=self.hybrid_user_id,
                agency_ids=self.agency_ids,
            )
        ]
        if self.hybrid_host_2:
            out.append(
                TenantConfig(
                    label=self.hybrid_agency_label_2 or self.hybrid_host_2,
                    host=self.hybrid_host_2,
                    cookies=self.hybrid_cookies_2,
                    user_id=self.hybrid_user_id_2,
                    agency_ids=[x.strip() for x in self.hybrid_agency_ids_2.split(",") if x.strip()],
                )
            )
        return out


class TenantConfig:
    """Один кабинет (host + куки + scope agency_ids)."""
    def __init__(self, label: str, host: str, cookies: str, user_id: str, agency_ids: list[str]):
        self.label = label
        self.host = host
        self.cookies = cookies
        self.user_id = user_id
        self.agency_ids = agency_ids


settings = Settings()  # type: ignore[call-arg]
