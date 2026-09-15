/**
 * Turn an element's source into a component *type*.
 *
 * `generateElement` hands back `createElement(Component)` -- an element, not a
 * type -- and re-rendering that one cached element object is what froze the old
 * `<Runner>`: React bails out of a subtree when it is handed the identical
 * element again. Unwrapping it to the type lets the wrapper render it afresh,
 * so the component stays mounted and re-reads the scope it closed over.
 *
 * The type is deliberately **not** shared between instances of one name.
 * react-runner evaluates the source as
 * `new Function(...scopeKeys, code)(...scopeValues)`, so the exported component
 * closes over the scope it was compiled with: two cards sharing a compiled type
 * would share one `props` object and one `updateElement`, and the second card
 * would render the first one's data. Sharing happens at the fetch
 * (`source.ts`), never here.
 */
import type { ComponentType } from 'react';
import { type Scope, generateElement } from 'react-runner';

export const compileElement = (code: string, scope: Scope): ComponentType => {
  const element = generateElement({ code, scope });

  if (element && typeof element.type === 'function') {
    return element.type as ComponentType;
  }

  // A source that exports a ready element or a string rather than a component:
  // there is nothing to re-render, so hand back a constant.
  return () => element;
};
