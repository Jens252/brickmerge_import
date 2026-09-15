"""EU country check and ISO-2 normalization."""

# Map english names to ISO-2
COUNTRY_TO_ISO2 = {
    'AUSTRIA': 'AT', 'BELGIUM': 'BE', 'BULGARIA': 'BG', 'CROATIA': 'HR', 'CYPRUS': 'CY', 'CZECH REPUBLIC': 'CZ',
    'CZECHIA': 'CZ', 'DENMARK': 'DK', 'ESTONIA': 'EE', 'FINLAND': 'FI', 'FRANCE': 'FR', 'GERMANY': 'DE',
    'GREECE': 'GR', 'HUNGARY': 'HU', 'IRELAND': 'IE', 'ITALY': 'IT', 'LATVIA': 'LV', 'LITHUANIA': 'LT',
    'LUXEMBOURG': 'LU', 'MALTA': 'MT', 'NETHERLANDS': 'NL', 'POLAND': 'PL', 'PORTUGAL': 'PT', 'ROMANIA': 'RO',
    'SLOVAKIA': 'SK', 'SLOVENIA': 'SI', 'SPAIN': 'ES', 'SWEDEN': 'SE',
}

EU_ISO2_CODES = set(COUNTRY_TO_ISO2.values()) | {'EL'}

def to_iso2(country: str | None) -> str | None:
    """Normalizes EU country name to uppercase ISO-2."""
    if not country or not isinstance(country, str):
        return None

    c = country.strip().upper()

    # Already valid ISO-2 code
    if len(c) == 2:
        if c == 'EL':
            return 'GR'
        return c if c in EU_ISO2_CODES else None

    # Mapping via lookup table
    return COUNTRY_TO_ISO2.get(c, None)


def is_eu(country: str | None) -> bool:
    """Checks if a country belongs to the European Union (returns True/False)."""
    return to_iso2(country) is not None