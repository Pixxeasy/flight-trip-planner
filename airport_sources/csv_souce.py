from .airport_source import AirportSource
class CsvAirportSource(AirportSource):
    filepath: str

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._by_code: dict[str, dict] = {}

    def read(self) -> None:
        import csv
        try:
            with open(self.filepath, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self._by_code = {
                row["IATA"]: row
                for row in reader
                if row["IATA"]
                }
              
        except FileNotFoundError:
            raise FileNotFoundError(f"CSV file not found: {self.filepath}")

    def get_city(self, iata_code: str) -> str:
        try:
            return self._by_code[iata_code]["City_Name"]
        except KeyError:
            raise KeyError(f"No airport found for IATA code: {iata_code}")

    def get_iata_codes(self, city: str) -> list[str]:
        return [
            code
            for code, row in self._by_code.items()
            if row["City_Name"] == city
        ]