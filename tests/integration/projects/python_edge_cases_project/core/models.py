# --- Static Analysis Expected ---
#
# Defined entities:
#   Classes (3): Config (@dataclass), UserProfile, UserProfile.Address (nested class)
#   Methods (10): Address.__init__, Address.full_address,
#                 UserProfile.__init__, UserProfile.name (@property),
#                 UserProfile.name.setter, UserProfile.email (@property),
#                 UserProfile.set_address, UserProfile.get_summary,
#                 UserProfile.__eq__, UserProfile.__hash__
#   Dataclass fields (3): Config.debug, Config.max_retries, Config.tags
#
# Expected call edges (from method bodies):
#   set_address   → Address()       (constructs nested class)
#   get_summary   → full_address()  (self._address.full_address())
#
# Class hierarchy:
#   Config         — standalone @dataclass, no inheritance
#   UserProfile    — standalone class, no inheritance
#   Address        — nested class inside UserProfile, no inheritance
#
# Corner cases: nested class, @property + setter, @dataclass, magic methods (__eq__, __hash__)
# Package: core | imports: dataclasses
# ---
from dataclasses import dataclass, field


@dataclass
class Config:
    debug: bool = False
    max_retries: int = 3
    tags: list[str] = field(default_factory=list)


class UserProfile:
    class Address:
        def __init__(self, street: str, city: str):
            self.street = street
            self.city = city

        def full_address(self) -> str:
            return f"{self.street}, {self.city}"

    def __init__(self, name: str, email: str):
        self._name = name
        self._email = email
        self._address: UserProfile.Address | None = None

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    @property
    def email(self) -> str:
        return self._email

    def set_address(self, street: str, city: str):
        self._address = UserProfile.Address(street, city)

    def get_summary(self) -> str:
        addr = self._address.full_address() if self._address else "No address"
        return f"{self._name} ({self._email}) - {addr}"

    def __eq__(self, other) -> bool:
        if not isinstance(other, UserProfile):
            return False
        return self._email == other._email

    def __hash__(self) -> int:
        return hash(self._email)
