# --- Static Analysis Expected ---
#
# Defined entities:
#   Classes (5): SwimmingMixin, Animal, Dog, Cat, Duck
#   Methods (8): SwimmingMixin.swim, Animal.__init__, Animal.speak (abstract),
#                Animal.__str__, Animal.__repr__, Dog.speak, Dog.fetch (@staticmethod),
#                Dog.create (@classmethod), Cat.speak, Duck.speak, Duck.actions
#                (note: overridden names like "speak" collapse to one reference key)
#
# Expected call edges (from method bodies):
#   __repr__      → __str__       (self.__str__())
#   create        → Dog/__init__  (cls(name) — classmethod constructor call)
#   actions       → speak         (self.speak())
#   actions       → swim          (self.swim())
#
# Class hierarchy:
#   Animal(ABC)              — abstract base class, superclass of Dog, Cat, Duck
#   Dog(Animal)              — single inheritance
#   Cat(Animal)              — single inheritance
#   Duck(Animal, SwimmingMixin) — multiple inheritance
#   SwimmingMixin            — mixin, subclassed by Duck
#
# Package: core | imports: abc
# ---
from abc import ABC, abstractmethod


class SwimmingMixin:
    def swim(self):
        return "swimming"


class Animal(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def speak(self) -> str:
        pass

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"

    def __repr__(self) -> str:
        return self.__str__()


class Dog(Animal):
    def speak(self) -> str:
        return "woof"

    @staticmethod
    def fetch(item: str) -> str:
        return f"fetching {item}"

    @classmethod
    def create(cls, name: str) -> "Dog":
        return cls(name)


class Cat(Animal):
    def speak(self) -> str:
        return "meow"


class Duck(Animal, SwimmingMixin):
    def speak(self) -> str:
        return "quack"

    def actions(self) -> list[str]:
        return [self.speak(), self.swim()]
