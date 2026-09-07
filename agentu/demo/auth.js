const prefix = "agentu.";
const random = () => crypto.randomUUID();
const b64 = (bytes) =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
export async function signIn(config) {
  const verifier = b64(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = b64(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)),
    ),
  );
  const state = random();
  sessionStorage.setItem(prefix + "pkce", verifier);
  sessionStorage.setItem(prefix + "oauth-state", state);
  const query = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: "openid email aws.cognito.signin.user.admin",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  location.assign(config.authDomain + "/oauth2/authorize?" + query);
}
export async function finishSignIn(config) {
  const query = new URLSearchParams(location.search);
  if (query.has("error")) {
    history.replaceState({}, "", location.pathname);
    throw new Error("Sign-in was not completed. Please try again.");
  }
  if (!query.has("code")) return;
  if (
    !query.get("state") ||
    query.get("state") !== sessionStorage.getItem(prefix + "oauth-state")
  )
    throw new Error("Sign-in session did not match. Start sign-in again.");
  const code = query.get("code");
  history.replaceState({}, "", location.pathname);
  const verifier = sessionStorage.getItem(prefix + "pkce");
  sessionStorage.removeItem(prefix + "pkce");
  sessionStorage.removeItem(prefix + "oauth-state");
  const result = await fetch(config.authDomain + "/oauth2/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
      code_verifier: verifier,
    }),
  });
  if (!result.ok)
    throw new Error("Sign-in could not be completed. Please start again.");
  const tokens = await result.json();
  sessionStorage.setItem(prefix + "access", tokens.access_token);
  sessionStorage.setItem(
    prefix + "expires",
    String(Date.now() + tokens.expires_in * 1000),
  );
}
export function token() {
  return Number(sessionStorage.getItem(prefix + "expires")) > Date.now() + 30000
    ? sessionStorage.getItem(prefix + "access")
    : null;
}
export function workspace(fresh = false) {
  let value = sessionStorage.getItem(prefix + "workspace");
  if (!value || fresh) {
    value = random();
    sessionStorage.setItem(prefix + "workspace", value);
  }
  return value;
}
export function signOut(config) {
  Object.keys(sessionStorage)
    .filter((k) => k.startsWith(prefix))
    .forEach((k) => sessionStorage.removeItem(k));
  if (config.mode === "hosted")
    location.assign(
      config.authDomain +
        "/logout?" +
        new URLSearchParams({
          client_id: config.clientId,
          logout_uri: config.logoutUri,
        }),
    );
  else location.reload();
}
