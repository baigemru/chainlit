describe('OAuth Auth Error UX (#1273)', () => {
  describe('OAuth callback failure paths redirect instead of returning raw JSON', () => {
    it('redirects on provider-returned error param', () => {
      cy.request({
        url: '/auth/oauth/github/callback?error=access_denied',
        followRedirect: false,
        failOnStatusCode: false
      }).then((response) => {
        expect(response.status).to.equal(302);
        expect(response.headers['location']).to.include(
          '/login?error=oauthSignin'
        );
      });
    });

    it('redirects on invalid state (no raw JSON 401) — regression for #1273', () => {
      cy.request({
        url: '/auth/oauth/github/callback?code=fake_code&state=fake_state',
        followRedirect: false,
        failOnStatusCode: false
      }).then((response) => {
        expect(response.status).to.equal(302);
        expect(response.headers['location']).to.include(
          '/login?error=oauthSignin'
        );
        expect(response.headers['content-type'] || '').to.not.include(
          'application/json'
        );
      });
    });
  });

  describe('login page renders friendly OAuth error message', () => {
    it('shows a specific message for oauthSignin error', () => {
      // The global beforeEach visits '/', and this app requires login, so
      // AppWrapper assigns window.location.href = '/login' once /auth/config
      // and /user have resolved — after cy.visit('/') already returned. Let
      // that redirect land first: arriving late it would clobber the visit
      // below and strip the query string the page reads the error from.
      cy.location('pathname').should('eq', '/login');

      // The message is translated, and the app picks the locale from
      // navigator.language — on a non-English machine the assertion below
      // would compare against a different translation.
      cy.visit('/login?error=oauthSignin', {
        onBeforeLoad(win) {
          Object.defineProperty(win.navigator, 'language', {
            value: 'en-US',
            configurable: true
          });
          Object.defineProperty(win.navigator, 'languages', {
            value: ['en-US'],
            configurable: true
          });
        }
      });
      cy.location('search').should('eq', '?error=oauthSignin');
      cy.get('[role="alert"]').should(
        'contain',
        'Sign in failed. Please try again, or use a different sign-in method.'
      );
      cy.get('body').should('not.contain', '{"detail"');
    });
  });
});
