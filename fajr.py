"""Compute today's Fajr time for a location.

Pure computation, no network. A high-latitude rule is applied so the calculation
still returns a sensible time in London during summer, when the sun never dips to
the Fajr angle below the horizon and the plain angle method has no solution.
"""

from datetime import datetime

from adhanpy.PrayerTimes import PrayerTimes
from adhanpy.calculation.CalculationMethod import CalculationMethod
from adhanpy.calculation.CalculationParameters import CalculationParameters
from adhanpy.calculation.HighLatitudeRule import HighLatitudeRule


def fajr_for(
    when: datetime,
    latitude: float,
    longitude: float,
    method: str,
    high_latitude_rule: str,
) -> datetime:
    """Return the timezone-aware Fajr datetime for the calendar day of ``when``.

    ``when`` must be timezone-aware; its date and zone define the day computed for.
    ``method`` and ``high_latitude_rule`` are enum names, e.g. "MUSLIM_WORLD_LEAGUE"
    and "SEVENTH_OF_THE_NIGHT".
    """
    params = CalculationParameters(method=CalculationMethod[method])
    # The method sets the angles; the high-latitude rule is separate and must be
    # set after construction, or the summer calculation returns nonsense in London.
    params.high_latitude_rule = HighLatitudeRule[high_latitude_rule]

    prayer_times = PrayerTimes(
        (latitude, longitude),
        when,
        calculation_parameters=params,
        time_zone=when.tzinfo,
    )
    return prayer_times.fajr
