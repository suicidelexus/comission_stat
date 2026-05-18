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

    @property
    def agency_ids(self) -> list[str]:
        return [x.strip() for x in self.hybrid_agency_ids.split(",") if x.strip()]


settings = Settings()  # type: ignore[call-arg]
