# -*- coding: utf-8 -*-
"""Slash-command parsing and dispatch utilities."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Coroutine


class CommandCategory(Enum):
    """Supported categories for built-in commands."""
    SESSION_CONTROL = "session_control"
    MODE_SWITCH = "mode_switch"
    INFO_QUERY = "info_query"
    SESSION_MANAGEMENT = "session_management"
    DEBUG = "debug"


@dataclass
class CommandDefinition:
    """Metadata and behavior for a registered command."""
    name: str
    category: CommandCategory
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    requires_llm: bool = False
    bypass_debounce: bool = True
    strip_from_message: bool = True
    handler: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None
    
    @property
    def all_names(self) -> list[str]:
        """Return the primary command name plus all aliases."""
        return [self.name] + self.aliases


@dataclass
class ParsedCommand:
    """Parsed representation of one command invocation."""
    command: str
    name: str
    args: list[str] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    remaining_text: str = ""
    is_standalone: bool = False
    definition: Optional[CommandDefinition] = None


class CommandParser:
    """Parse and execute slash-style chat commands.

    This parser handles commands such as `/new`, `/reset`, and `/model`, and
    supports command registration plus dispatch.

    Example:
        ```python
        parser = CommandParser()
        parser.register_default_commands()

        result = parser.parse("/model gpt-4 Hello World")
        # result.name == "model"
        # result.args == ["gpt-4"]
        # result.remaining_text == "Hello World"
        ```
    """
    
    # Match slash commands with an optional argument string.
    COMMAND_PATTERN = re.compile(r'^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?$', re.DOTALL)
    
    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}
        self._alias_map: dict[str, str] = {}
        
    def register(self, definition: CommandDefinition) -> None:
        """Register a command definition and its aliases."""
        self._commands[definition.name] = definition
        for alias in definition.aliases:
            self._alias_map[alias] = definition.name
            
    def unregister(self, name: str) -> bool:
        """

register
        
        Args:
            name:command name
            
        Returns:
            
        
"""
        if name in self._commands:
            definition = self._commands.pop(name)
            for alias in definition.aliases:
                self._alias_map.pop(alias, None)
            return True
        return False
        
    def get_definition(self, name: str) -> Optional[CommandDefinition]:
        """

getCommand definition
        
        Args:
            name:command nameor
            
        Returns:
            Command definition, to return None
        
"""
        # check
        if name in self._alias_map:
            name = self._alias_map[name]
        return self._commands.get(name)
        
    def parse(self, message: str) -> Optional[ParsedCommand]:
        """

parsemessagein
        
        supportformat:
        - /command
        - /command arg1 arg2
        - /command --key=value
        - /command arg1 remaining text
        
        Args:
            message:rawmessage
            
        Returns:
            parsed command, if return None
        
"""
        if not message:
            return None
            
        message = message.strip()
        if not message.startswith('/'):
            return None
            
        match = self.COMMAND_PATTERN.match(message)
        if not match:
            return None
            
        name = match.group(1).lower()
        rest = match.group(2) or ""
        
        # parseparameter
        args, kwargs, remaining = self._parse_args(rest)
        
        # getCommand definition
        definition = self.get_definition(name)
        
        # 
        is_standalone = not remaining.strip()
        
        return ParsedCommand(
            command=f"/{name}",
            name=name,
            args=args,
            kwargs=kwargs,
            remaining_text=remaining.strip(),
            is_standalone=is_standalone,
            definition=definition
        )
        
    def _parse_args(self, text: str) -> tuple[list[str], dict[str, Any], str]:
        """

parseparametercharacters
        
        Returns:
            (args, kwargs, remaining)
        
"""
        args: list[str] = []
        kwargs: dict[str, Any] = {}
        remaining_parts: list[str] = []
        in_remaining = False
        
        if not text:
            return args, kwargs, ""
            
        parts = text.split()
        
        for part in parts:
            if in_remaining:
                remaining_parts.append(part)
                continue
                
            # check --key=value for mat
            if part.startswith('--') and '=' in part:
                key_value = part[2:]
                key, _, value = key_value.partition('=')
                if key:
                    kwargs[key] = value
            elif part.startswith('--'):
                # --flag()
                key = part[2:]
                if key:
                    kwargs[key] = True
            elif part.startswith('-') and len(part) == 2:
                # -f()
                kwargs[part[1]] = True
            elif args or kwargs:
                # parameter,
                in_remaining = True
                remaining_parts.append(part)
            else:
                # parameter
                args.append(part)
                
        return args, kwargs, ' '.join(remaining_parts)
        
    def should_bypass_debounce(self, command: ParsedCommand) -> bool:
        """

check
        
        Args:
            command:parsed command
            
        Returns:
            
        
"""
        if command.definition:
            return command.definition.bypass_debounce
        # register default
        return True
        
    def should_strip_from_message(self, command: ParsedCommand) -> bool:
        """

check frommessagein
        
        Args:
            command:parsed command
            
        Returns:
            
        
"""
        if command.definition:
            return command.definition.strip_from_message
        # register default
        return False
        
    def requires_llm(self, command: ParsedCommand) -> bool:
        """

check LLM
        
        Args:
            command:parsed command
            
        Returns:
            LLM
        
"""
        if command.definition:
            return command.definition.requires_llm
        # register LLM(agent)
        return True
        
    def list_commands(self, category: Optional[CommandCategory] = None) -> list[CommandDefinition]:
        """


        
        Args:
            category:optional, filter
            
        Returns:
            Command definitionlist
        
"""
        commands = list(self._commands.values())
        if category:
            commands = [c for c in commands if c.category == category]
        return sorted(commands, key=lambda c: c.name)
        
    def register_default_commands(self) -> None:
        """registerdefault"""
        
        # session
        self.register(CommandDefinition(
            name="new",
            category=CommandCategory.SESSION_CONTROL,
            description="Reset session with optional new model",
            aliases=["reset"],
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        # mode
        self.register(CommandDefinition(
            name="think",
            category=CommandCategory.MODE_SWITCH,
            description="Toggle thinking mode (on/off)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="verbose",
            category=CommandCategory.MODE_SWITCH,
            description="Toggle verbose tool summary (on/off)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="reasoning",
            category=CommandCategory.MODE_SWITCH,
            description="Control reasoning visibility (on/off/stream)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="model",
            category=CommandCategory.MODE_SWITCH,
            description="Switch current session model",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        # 
        self.register(CommandDefinition(
            name="status",
            category=CommandCategory.INFO_QUERY,
            description="Show session status and context usage",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="context",
            category=CommandCategory.INFO_QUERY,
            description="Show context breakdown (list/detail)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="usage",
            category=CommandCategory.INFO_QUERY,
            description="Show token usage statistics",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        # session management
        self.register(CommandDefinition(
            name="compact",
            category=CommandCategory.SESSION_MANAGEMENT,
            description="Manually trigger compaction with optional instructions",
            requires_llm=True,  # LLM generate summary
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="queue",
            category=CommandCategory.SESSION_MANAGEMENT,
            description="Set queue mode (collect/steer/followup)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="stop",
            category=CommandCategory.SESSION_MANAGEMENT,
            description="Abort current run",
            aliases=["abort", "cancel"],
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        self.register(CommandDefinition(
            name="send",
            category=CommandCategory.SESSION_MANAGEMENT,
            description="Override session send strategy (on/off/inherit)",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
        
        # 
        self.register(CommandDefinition(
            name="debug",
            category=CommandCategory.DEBUG,
            description="Show debug information",
            requires_llm=False,
            bypass_debounce=True,
            strip_from_message=True
        ))
