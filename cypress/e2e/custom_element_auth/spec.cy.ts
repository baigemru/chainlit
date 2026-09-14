import { onServerFrames } from '../../support/testUtils';

/**
 * A custom element may not be turned into an arbitrary file read.
 *
 * The session id used to be readable from the request that set the sticky
 * cookie; that endpoint is gone, and the id is now minted by the server and
 * announced in `session.ready`. Taking it off the socket is the point: this
 * spec plays the attacker who already holds a legitimate handle on their own
 * session and tries to widen it into a read of a path they named themselves.
 */
describe('Custom Element Auth', () => {
  it('should not allow arbitrary file read', () => {
    let chainlitKey: string | null = null;
    let sessionId: string | null = null;

    onServerFrames({
      'element.upsert': (frame) => {
        chainlitKey = frame.element?.chainlitKey ?? null;
      },
      'session.ready': (frame) => {
        sessionId = frame.sessionId ?? null;
      }
    });

    cy.intercept('POST', '/login').as('login');

    cy.get('input[name="email"]').type('admin');
    cy.get('input[name="password"]').type('admin');
    cy.get('button[type="submit"]').click();

    cy.get('.step').should('have.length', 1);

    cy.wrap(null).should(() => {
      expect(sessionId).to.not.equal(null);
    });

    cy.then(() => {
      cy.request({
        method: 'PUT',
        url: '/project/element',
        body: {
          element: {
            type: 'custom',
            id: 'test',
            name: 'test',
            display: 'inline',
            path: 'cypress/e2e/custom_element_auth/test.txt'
          },
          sessionId: sessionId
        }
      });
    });

    cy.wrap(null).should(() => {
      expect(chainlitKey).to.not.equal(null);
    });

    cy.then(() => {
      cy.request({
        method: 'GET',
        url: `/project/file/${chainlitKey}`,
        qs: { session_id: sessionId }
      }).then((response) => {
        expect(response.body).to.not.equal('Test');
      });
    });
  });
});
