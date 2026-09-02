import React, { useMemo, useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import { theme } from '../theme.js';
import { Wordmark } from './Wordmark.js';
import {
  backspace,
  caretSplit,
  deleteWordBefore,
  EMPTY,
  end,
  home,
  insert,
  killToStart,
  left,
  right,
  type Edit,
} from '../input/editor.js';

/**
 * Full-screen launch picker: choose which Manager session (an independent
 * persistent conversation) to open, or name a brand-new one. The final row is a
 * `＋ new session…` affordance that turns into an inline text input on Enter;
 * typing a name and pressing Enter switches straight to that session (the
 * backend creates it on the first message, so no create call is needed here).
 *
 * The list UX mirrors the argus daemon selector: ↑/↓ or k/j move the highlight,
 * a `❯` marker + bold marks the focused row, the rest dim. Esc / Ctrl-C quits.
 */
export function SessionPicker({
  sessions,
  onSelect,
  health = '',
}: {
  sessions: string[];
  onSelect: (session: string) => void;
  health?: string;
}) {
  const { exit } = useApp();
  // Rows = every session name + one trailing "new session" affordance.
  const count = sessions.length + 1;
  const newIndex = sessions.length;
  const [selected, setSelected] = useState(0);
  const [naming, setNaming] = useState(false);
  const [edit, setEdit] = useState<Edit>(EMPTY);

  const move = (delta: number) => setSelected((s) => ((s + delta) % count + count) % count);

  const commitName = () => {
    const name = edit.value.trim();
    if (name) onSelect(name);
  };

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      exit();
      return;
    }

    // Inline name entry for the "＋ new session…" row.
    if (naming) {
      if (key.escape) {
        setNaming(false);
        setEdit(EMPTY);
        return;
      }
      if (key.return) {
        commitName();
        return;
      }
      if (key.leftArrow) return setEdit(left);
      if (key.rightArrow) return setEdit(right);
      if (key.ctrl && input === 'a') return setEdit(home);
      if (key.ctrl && input === 'e') return setEdit(end);
      if (key.ctrl && input === 'u') return setEdit(killToStart);
      if (key.ctrl && input === 'w') return setEdit(deleteWordBefore);
      if (key.backspace || key.delete) return setEdit(backspace);
      if (input && !key.ctrl && !key.meta) return setEdit((e) => insert(e, input));
      return;
    }

    if (key.escape) {
      exit();
      return;
    }
    if (key.downArrow || input === 'j') return move(1);
    if (key.upArrow || input === 'k') return move(-1);
    if (key.return) {
      if (selected === newIndex) {
        setNaming(true);
        setEdit(EMPTY);
      } else {
        onSelect(sessions[selected]);
      }
    }
  });

  return (
    <Box flexDirection="column" paddingX={1}>
      <Box>
        <Wordmark />
        <Text dimColor>{'   '}</Text>
        <Text color={theme.muted}>select a Manager session</Text>
      </Box>
      {health ? <Text color={theme.warning}>{`  ⚠ ${health}`}</Text> : null}
      <Box
        flexDirection="column"
        borderStyle="round"
        borderColor={theme.border}
        paddingX={1}
        marginTop={1}
      >
        {sessions.length === 0 ? (
          <Text dimColor>(no sessions reported — create one below)</Text>
        ) : (
          sessions.map((name, i) => {
            const on = i === selected && !naming;
            return (
              <Box key={name}>
                <Text color={on ? theme.accent : theme.dim}>{on ? '❯ ' : '  '}</Text>
                <Text color={on ? theme.accent : theme.role.manager} bold={on} dimColor={!on}>
                  {`● ${name}`}
                </Text>
              </Box>
            );
          })
        )}
        <NewSessionRow
          focused={selected === newIndex}
          naming={naming}
          edit={edit}
        />
      </Box>
      <Text dimColor>{'  ↑↓ select · Enter open · type after ＋ to name · Esc quit'}</Text>
    </Box>
  );
}

/** The trailing `＋ new session…` row — a static affordance, or an inline caret input. */
function NewSessionRow({ focused, naming, edit }: { focused: boolean; naming: boolean; edit: Edit }) {
  if (naming) {
    const { before, at, after } = caretSplit(edit);
    return (
      <Box>
        <Text color={theme.accent}>{'❯ ＋ '}</Text>
        {edit.value.length === 0 && !at ? <Text dimColor>name the session… </Text> : null}
        <Text>{before}</Text>
        {at ? <Text inverse>{at}</Text> : <Text color={theme.accent}>▏</Text>}
        <Text>{after}</Text>
      </Box>
    );
  }
  return (
    <Box>
      <Text color={focused ? theme.accent : theme.dim}>{focused ? '❯ ' : '  '}</Text>
      <Text color={focused ? theme.accent : theme.dim} bold={focused} dimColor={!focused}>
        ＋ new session…
      </Text>
    </Box>
  );
}
