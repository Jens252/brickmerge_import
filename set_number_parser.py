"""Parser for extracting LEGO set numbers from seller SKUs and product titles."""

from __future__ import annotations

import re
import pandas as pd


class SetNumberParser:
    """Extracts LEGO set numbers using user-defined template patterns or title heuristics.

    Supported template tokens:
      - {SET}: Base LEGO set number (4 to 7 digits)
      - {ZAHL,min,max}: Digits only (default length: 1-4)
      - {TEXT,min,max}: Alphabetical characters only (default length: 1-4)
      - {WERT,min,max}: Alphanumeric characters (default length: 1-4)
      - {*,min,max} or *: Any alphanumeric characters including dash/underscore
      - [...] : Optional segments, e.g. '[-{WERT,1,4}]'
    """

    def __init__(self, sku_template: str | None = None) -> None:
        self.sku_pattern: re.Pattern[str] = self.compile_sku_template(
            sku_template or "{SET}[-{WERT,1,4}]"
        )

    @classmethod
    def compile_sku_template(cls, template: str | None) -> re.Pattern[str]:
        """Converts a user-friendly SKU template string into a compiled regex pattern.

        Examples:
            >>> SetNumberParser.compile_sku_template('{SET}[-{WERT,1,4}]')
            re.compile('^(?P<set>\\d{4,7})(?:-[a-zA-Z0-9]{1,4})?$', re.IGNORECASE)
        """
        t = (template or "").strip() or "{SET}"

        replacements: dict[str, str] = {}
        counter = 0

        def parse_token(match: re.Match[str]) -> str:
            nonlocal counter
            content = match.group(1).strip()
            parts = [p.strip() for p in content.split(",")]
            token_name = parts[0].upper()

            min_len = (
                parts[1] if len(parts) > 1 and parts[1].isdigit() else "1"
            )
            max_len = parts[2] if len(parts) > 2 and parts[2].isdigit() else ""
            quantifier = (
                f"{{{min_len},{max_len}}}" if max_len else f"{{{min_len},}}"
            )

            if token_name == "SET":
                if len(parts) > 1:
                    # Use min and max if {SET,min,max} is given
                    regex_fragment = rf"(?P<set>\d{quantifier})"
                else:
                    # Default: 4 to 7 digits
                    regex_fragment = r"(?P<set>\d{4,7})"
            elif token_name == "ZAHL":
                default_quant = (
                    f"{{{min_len},{max_len}}}" if len(parts) > 1 else "{1,4}"
                )
                regex_fragment = rf"\d{default_quant}"
            elif token_name == "TEXT":
                default_quant = (
                    f"{{{min_len},{max_len}}}" if len(parts) > 1 else "{1,4}"
                )
                regex_fragment = rf"[a-zA-Z]{default_quant}"
            elif token_name in ("WERT", "ALNUM"):
                default_quant = (
                    f"{{{min_len},{max_len}}}" if len(parts) > 1 else "{1,4}"
                )
                regex_fragment = rf"[a-zA-Z0-9]{default_quant}"
            elif token_name == "*":
                regex_fragment = rf"[a-zA-Z0-9_-]{quantifier}"
            else:
                return match.group(0)

            token_id = f"__TOKEN_{counter}__"
            replacements[token_id] = regex_fragment
            counter += 1
            return token_id

        # 1. Mask optional groups: [...] -> non-capturing group (?:...)?
        t = t.replace("[", "__OPT_OPEN__").replace("]", "__OPT_CLOSE__")

        # 2. Mask parameterized tokens
        t = re.sub(r"\{([^}]+)\}", parse_token, t)

        # 3. Mask standalone wildcards
        if "*" in t:
            token_id = f"__TOKEN_{counter}__"
            replacements[token_id] = r"[a-zA-Z0-9_-]*"
            t = t.replace("*", token_id)

        # 4. Escape remaining literals (hyphens, dots, etc.)
        t = re.escape(t)

        # 5. Restore compiled regex fragments
        for token_id, pattern in replacements.items():
            t = t.replace(token_id, pattern)

        # 6. Restore optional group enclosures
        t = t.replace("__OPT_OPEN__", "(?:").replace("__OPT_CLOSE__", ")?")

        return re.compile(f"^{t}$", re.IGNORECASE)

    def get_set_number_from_sku(self, sku: str | None) -> str | None:
        """Extracts the base LEGO set number from a seller SKU using the active template."""
        if not sku or pd.isna(sku):
            return None

        match = self.sku_pattern.match(str(sku).strip())
        if match and "set" in match.groupdict():
            return match.group("set")

        return None

    @staticmethod
    def get_set_number_from_title(title: str | None) -> str | None:
        """Extracts LEGO set numbers (5+ digits) from product titles as a fallback.

        Prefers exact 5-digit matches if multiple candidate numbers (e.g. years) are found.
        """
        if (
                not title
                or not isinstance(title, str)
                or "lego" not in title.lower()
        ):
            return None

        matches = re.findall(r"\b\d{5,}\b", title)
        if len(matches) == 1:
            return matches[0]

        # Disambiguate when title contains multiple numbers: prefer 5-digit number
        five_digit_matches = [m for m in matches if len(m) == 5]
        if len(five_digit_matches) == 1:
            return five_digit_matches[0]

        return None