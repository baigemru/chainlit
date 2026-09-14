import { submitMessage } from '../../support/testUtils';

function login() {
  return cy.request({
    method: 'GET',
    url: '/auth/custom',
    followRedirect: false
  });
}

// `/auth/token` had one caller: the embeddable widget, which authenticated
// with a bearer token because it ran on somebody else's page. The widget is
// gone and the app is cookie-only, so nothing here mints a token any more.

function shouldShowGreetingMessage() {
  it('should show greeting message', () => {
    cy.get('.step').should('exist');
    cy.get('.step').should('contain', 'Hello');
  });
}

function shouldSendMessageAndRecieveAnswer() {
  it('should send message and receive answer', () => {
    cy.get('.step').should('contain', 'Hello');

    const testMessage = 'Test message from custom auth';
    submitMessage(testMessage);

    cy.get('.step').should('contain', 'Echo:');
    cy.get('.step').should('contain', testMessage);
  });
}

describe('Custom Auth', () => {
  describe('when unauthenticated', () => {
    beforeEach(() => {
      cy.intercept('GET', '/user').as('user');
    });

    it('should attempt to and not have permission to access /user', () => {
      cy.wait('@user').then((interception) => {
        expect(interception.response.statusCode).to.equal(401);
      });
    });

    it('should redirect to login dialog', () => {
      cy.location('pathname').should('eq', '/login');
    });
  });

  describe('authenticating via custom endpoint', () => {
    beforeEach(() => {
      login().then((response) => {
        expect(response.status).to.equal(200);
        // Verify cookie is set in response headers
        expect(response.headers).to.have.property('set-cookie');
        const cookies = Array.isArray(response.headers['set-cookie'])
          ? response.headers['set-cookie']
          : [response.headers['set-cookie']];
        expect(cookies[0]).to.contain('access_token');
      });
    });

    const shouldBeLoggedIn = () => {
      it('should not be on /login', () => {
        cy.location('pathname').should('not.contain', '/login');
      });

      shouldShowGreetingMessage();
    };

    shouldBeLoggedIn();

    it('should request and have access to /user', () => {
      cy.intercept('GET', '/user').as('user');
      cy.wait('@user').then((interception) => {
        expect(interception.response.statusCode).to.equal(200);
      });
    });

    shouldSendMessageAndRecieveAnswer();

    describe('after reloading', () => {
      beforeEach(() => {
        cy.reload();
      });

      shouldBeLoggedIn();
    });
  });
});
