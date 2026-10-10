from abc import ABC, abstractmethod

class AirportSource(ABC):
    @abstractmethod
    def read(self) -> None:
        pass

    @abstractmethod
    def get_city(iata_code: str) -> str:
        pass

    @abstractmethod
    def get_iata_codes(city: str) -> list[str]:
        pass