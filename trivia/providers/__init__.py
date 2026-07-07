# trivia/providers/__init__.py - Provider pattern for multi-source trivia

from trivia.providers.base import TriviaProvider, StandardQuestion
from trivia.providers.opentdb import OpenTDBProvider
from trivia.providers.trivia_api import TriviaAPIProvider
from trivia.providers.quizapi import QuizAPIProvider

__all__ = [
    "TriviaProvider",
    "StandardQuestion",
    "OpenTDBProvider",
    "TriviaAPIProvider",
    "QuizAPIProvider",
]
