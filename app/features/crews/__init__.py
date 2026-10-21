"""Crews: a saved group of people (a family, a class, a city, friends) with one invite link (PRD Tr1). Members join once;
the crew page lists them. Nothing here gives points, XP or rewards."""
from .router import router  # noqa: F401

NAME = "crews"  # the feature flag
PERMISSIONS: dict[str, str] = {}
