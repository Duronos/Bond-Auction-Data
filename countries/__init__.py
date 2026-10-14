"""
One loader per country; each exposes a COUNTRY (see common.Country).

OECD members without a loader yet, and why, are listed in the README.
"""

from countries import (
    austria,
    belgium,
    canada,
    czechia,
    denmark,
    eu,
    finland,
    france,
    germany,
    greece,
    hungary,
    ireland,
    italy,
    japan,
    korea,
    lithuania,
    netherlands,
    norway,
    poland,
    slovakia,
    slovenia,
    spain,
    switzerland,
    uk,
    us,
)

# Order sets the legend and grid-panel order (and each country's fixed chart
# colour/line style): the original five first, then the rest alphabetically,
# then the European Union, which isn't a country but is treated like one.
ALL = [
    us.COUNTRY,
    canada.COUNTRY,
    uk.COUNTRY,
    germany.COUNTRY,
    japan.COUNTRY,
    austria.COUNTRY,
    belgium.COUNTRY,
    czechia.COUNTRY,
    denmark.COUNTRY,
    finland.COUNTRY,
    france.COUNTRY,
    greece.COUNTRY,
    hungary.COUNTRY,
    ireland.COUNTRY,
    italy.COUNTRY,
    lithuania.COUNTRY,
    netherlands.COUNTRY,
    norway.COUNTRY,
    poland.COUNTRY,
    slovakia.COUNTRY,
    slovenia.COUNTRY,
    korea.COUNTRY,  # "South Korea", so it sorts here
    spain.COUNTRY,
    switzerland.COUNTRY,
    eu.COUNTRY,
]
