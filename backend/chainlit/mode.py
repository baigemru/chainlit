"""Mode and ModeOption dataclasses for the Modes system.

The Modes system allows developers to define multiple picker categories
(e.g., Model, Approach, Reasoning Effort) that users can select from
in the chat composer.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ModeOption:
    """A single selectable option within a Mode.

    Attributes:
        id: Unique identifier for this option (e.g., "gpt-5", "planning")
        name: Display name shown in the UI (e.g., "GPT-5", "Planning")
        description: Optional description shown in the dropdown
        icon: Optional icon - can be a Lucide icon name, local path, or URL
        default: Whether this is the default selected option for its mode
    """

    id: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Mode:
    """A category of options the user can select from.

    Each Mode represents a picker dropdown in the chat composer.
    Users select exactly one option per mode.

    Attributes:
        id: Unique identifier for this mode (e.g., "llm", "approach")
        name: Display name shown in the UI (e.g., "Model", "Approach")
        options: List of available options for this mode
    """

    id: str
    name: str
    options: List[ModeOption] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def get_default_option(self) -> Optional[ModeOption]:
        """Get the default option for this mode, or the first option if none is default."""
        for option in self.options:
            if option.default:
                return option
        return self.options[0] if self.options else None

    def get_option_by_id(self, option_id: str) -> Optional[ModeOption]:
        """Get an option by its ID."""
        for option in self.options:
            if option.id == option_id:
                return option
        return None
