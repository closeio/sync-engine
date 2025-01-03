import abc
import datetime
from typing import Never


class AbstractContactsProvider(abc.ABC):
    PROVIDER_NAME: str

    @abc.abstractmethod
    def get_items(
        self,
        sync_from_dt: datetime.datetime | None = None,
        max_results: int = 100000,
    ) -> Never:
        raise NotImplementedError()
