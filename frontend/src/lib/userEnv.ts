/**
 * Whether every environment key the app requires has been supplied.
 *
 * One predicate for the two places that ask: `Page` sends the user to `/env`
 * while it is false, and the connect effect must not open a socket while it
 * is false either. A session is born with its `user_env` -- the server reads
 * it out of `hello` and never again -- so a socket opened before the keys
 * were typed would live its whole life without them, and re-attaching with
 * the keys only refreshes the payload of a connection that is already up.
 */
export const requiredEnvPresent = (
  required: string[] | undefined,
  userEnv: Record<string, string>
): boolean => (required ?? []).every((key) => !!userEnv[key]);
