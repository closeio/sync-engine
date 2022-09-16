import abc
import datetime
from typing import Optional


class AbstractContactsProvider(abc.ABC):
    PROVIDER_NAME: str

    @abc.abstractmethod
    def get_items(
        self,
        sync_from_dt: Optional[datetime.datetime] = None,
        max_results: int = 100000,
    ):
        raise NotImplementedError()
