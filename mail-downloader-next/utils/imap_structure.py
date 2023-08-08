"""A helper to parse structures returned from the IMAP server."""
from __future__ import annotations

from collections.abc import Generator, Sequence
from dataclasses import dataclass
from typing import Any, Optional, TypeAlias, TypeVar, Union, cast, overload

__all__ = [
    'Address',
    'BodyStructureType',
    'Envelope',
    'load_bodystructure',
    'load_envelope',
    'NonMultiPart',
    'MultiPart'
]

_T = TypeVar('_T')

_ParamType: TypeAlias = dict[str, Optional[Union[str, '_ParamType']]]

MULTIPART_SUBTYPES: list[str] = [
    'alternative',
    'byterange',
    'digest',
    'encrypted',
    'form-data',
    'mixed',
    'related',
    'report',
    'signed',
    'x-mixed-replace'
]


@dataclass(frozen=True)
class Address:
    username: str
    domain: str
    display_name: str | None = None
    smtp: Any = None  # FIXME: The real type is unknown

    @property
    def address(self) -> str:
        return '@'.join((
            self.username,
            self.domain
        ))

    def __str__(self) -> str:
        from .mail_helpers import decode_header
        display_name = self.display_name
        return ''.join((
            '<',
            '"{}" '.format(
                decode_header(display_name).strip('" ')
            )
            if display_name is not None
            else '',
            self.address,
            '>'
        ))


@dataclass(frozen=True)
class Envelope:
    """The envelope part of a message."""
    date: str | None = None
    subject: str | None = None
    from_: list[Address] | None = None
    sender: list[Address] | None = None
    reply_to: list[Address] | None = None
    to: list[Address] | None = None
    cc: list[Address] | None = None
    bcc: list[Address] | None = None
    in_replay_to: str | None = None
    message_id: str | None = None


_IS_MULTIPART = object()
_IS_MESSAGE = object()


@dataclass
class _BasePart:
    # the 'section' field is not belong to the document,
    # but it shows the section of the part in the whole email.
    section_list: list[int | str]

    @property
    def section_str(self) -> str:
        return '.'.join(map(str, self.section_list))


@dataclass
class NonMultiPart(_BasePart):
    """The non-multipart body part in a BodyStructure."""

    main_type: str | None
    sub_type: str | None
    params: _ParamType | None
    id: str | None
    description: str | None
    encoding: str | None
    size: int | None

    envelope_structure: Envelope | None = None
    envelope_body:  BodyStructureType | None = None
    text_line_size: int | None = None

    md5: int | None = None
    disposition: _ParamType | None = None
    language:  str | list[str] | None = None
    location: str | None = None

    def walk(self) -> Generator[BodyStructureType, None, None]:
        yield self
        if self.envelope_body is not None:
            yield from self.envelope_body.walk()


@dataclass
class MultiPart(_BasePart):
    """The multipart body part in a BodyStructure."""

    sub_type: str
    sub_parts: list[BodyStructureType]

    params: _ParamType | None = None
    disposition: _ParamType | None = None
    language: str | list[str] | None = None
    location: str | None = None

    def walk(self) -> Generator[BodyStructureType, None, None]:
        yield self
        for sub_part in self.sub_parts:
            yield from sub_part.walk()


BodyStructureType: TypeAlias = NonMultiPart | MultiPart


def tokenize(text: str, remove_identifier: bool = True) -> list[str]:
    """Primarily parsing structure.

    Args:
        text (str): A structure text.

    Returns:
        list[str]: A list with individual tokens.
    """
    # Convenient for debugging.
    text = text.replace('\n', ' ')
    # Remove the identifier like
    # '(BODYSTRUCTURE ...)' or
    # '(ENVELOPE ...)'.
    if remove_identifier:
        text = text[text.find(' ')+1:-1]

    res: list[str] = []
    quoted = False
    start = 0
    for end, char in enumerate(text):
        if quoted:
            if char == '"':
                res.append(text[start:end+1])
                start = end+1
                quoted = not quoted
        else:
            if char in '() ':  # Splitters
                # Check if the token has been processed.
                if start != end:
                    res.append(text[start:end])
                # Append brackets to tokens.
                if char != ' ':
                    res.append(char)
                start = end+1
            elif char == '"':
                start = end
                quoted = not quoted
    return res


def nest_tokens(tokens: Sequence[str]) -> list[Any]:
    """Remove the brackets and nest the tokens.

    Args:
        tokens (Sequence[str]): A list with individual tokens.

    Returns:
        _NestedListType[str]: A list containing
            tokens and nested lists.
    """
    res: list[Any] = []
    cur_list: list[list[Any]] = []
    cur_list.append(res)
    # Remove the outermost bracket.
    tokens = tokens[1: -1]
    for token in tokens:
        if token == '(':
            new_list: list[Any] = []
            cur_list[-1].append(new_list)
            cur_list.append(new_list)
        elif token == ')':
            cur_list.pop()
        else:
            cur_list[-1].append(token)
    return res


@overload
def _get_token(
    __token_or_tokens: _T,
    *,
    parse_double_quotes: bool = True
) -> _T | None: ...


@overload
def _get_token(
    __tokens: Sequence[_T],
    index: int,
    parse_double_quotes: bool = True
) -> _T | None: ...


def _get_token(
    token_or_tokens: str | Sequence[Any],
    /,
    index: int | None = None,
    parse_double_quotes: bool = True
) -> Any:
    if index is None:
        res = token_or_tokens
    else:
        try:
            res = token_or_tokens[index]
        except IndexError:
            return None
    if isinstance(res, str):
        if res.find('"') == -1:
            res = res.lower()
            if res == 'nil':
                res = None
        else:
            res = res.replace('"', '')
            if parse_double_quotes:
                res = res.lower()
                if res == 'unknown':
                    res = None
    return res


@overload
def parse_pair(
    tokens: None,
    parse_double_quotes: bool = False
) -> None: ...


@overload
def parse_pair(
    tokens: Sequence[Any],
    parse_double_quotes: bool = False
) -> _ParamType: ...


def parse_pair(
    tokens: Sequence[Any] | None,
    parse_double_quotes: bool = False
) -> _ParamType | None:
    if tokens is None:
        return tokens
    else:
        res: _ParamType = {}
        for k, v in zip(tokens[::2], tokens[1::2]):
            k = _get_token(k)
            assert isinstance(k, str)
            if isinstance(v, list):
                v = parse_pair(v, parse_double_quotes)
            else:
                v = _get_token(v, parse_double_quotes=parse_double_quotes)
            assert isinstance(v, (str, dict)) or v is None
            res[k] = v
        return res


def _load_bodystructure_from_nested_tokens(
        tokens: Sequence[Any],
        _section: list[int | str] | None = None,
        _parent_type: object = _IS_MESSAGE) -> BodyStructureType:
    """Perform final parsing on nested lists
    and convert them to a BodyStructure tree.
    """

    if _section is None:
        _section = []

    is_multipart = False
    for token in tokens:
        if isinstance(token, str):
            token = _get_token(token)
            if token in MULTIPART_SUBTYPES:
                is_multipart = True
                break

    i = 0
    if is_multipart:
        if _parent_type is _IS_MESSAGE:
            _section.append('text')
        sub_parts: list[BodyStructureType] = []
        new_sec = 1
        while True:
            token = _get_token(tokens, i)
            if isinstance(token, list):
                sub_parts.append(
                    _load_bodystructure_from_nested_tokens(
                        token, (
                            list(_section)
                            if _parent_type is not _IS_MESSAGE
                            else _section[:-1]
                        )+[new_sec], _IS_MULTIPART
                    )
                )
                i += 1
                new_sec += 1
            else:
                break
        sub_type = token
        assert isinstance(sub_type, str)
        i += 1
        params = _get_token(tokens, i)
        assert isinstance(params, list) or params is None
        params = parse_pair(params)
        i += 1
        disposition = _get_token(tokens, i)
        assert isinstance(disposition, list) or disposition is None
        disposition = parse_pair(disposition)
        i += 1
        language = _get_token(tokens, i)
        i += 1
        location = _get_token(tokens, i)
        assert isinstance(location, str) or location is None
        i += 1

        return MultiPart(
            _section,
            sub_type,
            sub_parts,
            params,
            disposition,
            language,
            location
        )
    else:
        if _parent_type is not _IS_MULTIPART:
            _section.append(1)

        main_type = _get_token(tokens, i)
        assert isinstance(main_type, str) or main_type is None
        i += 1
        sub_type = _get_token(tokens, i)
        assert isinstance(sub_type, str) or sub_type is None
        i += 1
        params = _get_token(tokens, i)
        assert isinstance(params, list) or params is None
        params = parse_pair(params)
        i += 1

        id_ = _get_token(tokens, i)
        assert isinstance(id_, str) or id_ is None
        i += 1
        description = _get_token(tokens, i)
        assert isinstance(description, str) or description is None
        i += 1
        encoding = _get_token(tokens, i)
        assert isinstance(encoding, str) or encoding is None
        i += 1
        size = _get_token(tokens, i)
        assert isinstance(size, str) or size is None
        if size is not None:
            size = int(size)
        i += 1

        envelope_structure = None
        envelope_body = None
        text_line_size = None
        if main_type == 'text':
            text_line_size = _get_token(tokens, i)
            assert isinstance(text_line_size, str) or text_line_size is None
            if text_line_size is not None:
                text_line_size = int(text_line_size)
            i += 1
        if (main_type, sub_type) in (
            ('message', 'rfc822'),
            ('message', 'global')
        ):
            envelope_structure = _get_token(tokens, i)
            assert isinstance(envelope_structure,
                              list) or envelope_structure is None
            if isinstance(envelope_structure, list):
                envelope_structure = _load_envelope_from_nested_tokens(
                    envelope_structure)
                i += 1
                envelope_body = _get_token(tokens, i)
                assert isinstance(envelope_body, list)
                envelope_body = _load_bodystructure_from_nested_tokens(
                    envelope_body, list(_section), _IS_MESSAGE)
                i += 1
                text_line_size = _get_token(tokens, i)
                assert isinstance(
                    text_line_size, str) or text_line_size is None
                if text_line_size is not None:
                    text_line_size = int(text_line_size)
                i += 1
        md5 = _get_token(tokens, i)
        assert isinstance(md5, str) or md5 is None
        if md5 is not None:
            md5 = int(md5)
        i += 1
        disposition = _get_token(tokens, i)
        assert isinstance(disposition, list) or disposition is None
        disposition = parse_pair(disposition)
        i += 1
        language = _get_token(tokens, i)
        i += 1
        location = _get_token(tokens, i)
        assert isinstance(location, str) or location is None
        i += 1

        return NonMultiPart(
            _section,
            main_type,
            sub_type,
            params,
            id_,
            description,
            encoding,
            size,
            envelope_structure,
            envelope_body,
            text_line_size,
            md5,
            disposition,
            language,
            location
        )


def _load_envelope_from_nested_tokens(tokens: Sequence[Any]) -> Envelope:
    """Perform final parsing on nested lists
    and convert them to an Envelope.
    """
    @overload
    def parse_address(tokens: None) -> None: ...

    @overload
    def parse_address(
        tokens: Sequence[Sequence[str]]) -> list[Address]: ...

    def parse_address(tokens: Sequence[Sequence[str]] | None) -> list[Address] | None:
        res: list[Address] = []
        if tokens is None:
            return tokens
        for addr in tokens:
            display_name, smtp, username, domain = (
                _get_token(token, parse_double_quotes=False) for token in addr)
            assert isinstance(username, str)
            assert isinstance(domain, str)
            assert isinstance(display_name, str) or display_name is None
            res.append(Address(username, domain, display_name, smtp))
        return res

    i = 0
    date = _get_token(tokens, i, False)
    assert isinstance(date, str) or date is None
    i += 1
    subject = _get_token(tokens, i, False)
    assert isinstance(subject, str) or subject is None
    i += 1
    from_ = _get_token(tokens, i)
    assert isinstance(from_, list) or from_ is None
    from_ = parse_address(from_)
    i += 1
    sender = _get_token(tokens, i)
    assert isinstance(sender, list) or sender is None
    sender = parse_address(sender)
    i += 1
    reply_to = _get_token(tokens, i)
    assert isinstance(reply_to, list) or reply_to is None
    reply_to = parse_address(reply_to)
    i += 1
    to = _get_token(tokens, i)
    assert isinstance(to, list) or to is None
    to = parse_address(to)
    i += 1
    cc = _get_token(tokens, i)
    assert isinstance(cc, list) or cc is None
    cc = parse_address(cc)
    i += 1
    bcc = _get_token(tokens, i)
    assert isinstance(bcc, list) or bcc is None
    bcc = parse_address(bcc)
    i += 1
    in_replay_to = _get_token(tokens, i, False)
    assert isinstance(in_replay_to, str) or in_replay_to is None
    i += 1
    message_id = _get_token(tokens, i, False)
    assert isinstance(message_id, str) or message_id is None
    i += 1

    return Envelope(
        date,
        subject,
        from_,
        sender,
        reply_to,
        to,
        cc,
        bcc,
        in_replay_to,
        message_id
    )


def load_bodystructure(text: str) -> BodyStructureType:
    """Generate BodyStructure tree from text.

    for non-multipart body part whose type is not 'message/rfc822'
    or 'message/global', fetching the message through the section
    field will get its leaf data directly.

    Args:
        text (str): A BODYSTRUCTURE text.
            A bodystructure is like this:
            (BODYSTRUCTURE ("text" "html" ("charset" "utf-8") 
                NIL NIL "base64" 334 5 NIL NIL NIL))

    Returns:
        _BodyStructureType: A BodyStructure tree.
    """
    tokens = nest_tokens(tokenize(text))
    return _load_bodystructure_from_nested_tokens(tokens)


def load_envelope(text: str) -> Envelope:
    """Generate Envelope from text.

    Args:
        text (str): An ENVELOPE text.

    Returns:
        Envelope: An Envelope.
    """
    tokens = nest_tokens(tokenize(text))
    return _load_envelope_from_nested_tokens(tokens)
