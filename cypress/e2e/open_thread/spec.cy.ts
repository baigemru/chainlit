import { submitMessage } from '../../support/testUtils';

const login = () => {
  cy.location('pathname').should('eq', '/login');
  cy.get('#email').should('be.visible').type('user1');
  cy.get('#password').should('be.visible').type('user1{enter}');
};

describe('Return to the parent thread', () => {
  it('shows the return button only with a parent, returns without duplicates and collapses the excursion', () => {
    login();

    cy.get('#chat-input').should('exist');
    cy.get('#chat-profiles').should('contain.text', 'Assistant');
    // A thread without a parent has no return button
    cy.get('[data-test="open-parent-thread"]').should('not.exist');

    submitMessage('hello');
    cy.get('.step').should('contain', 'profile: Assistant');
    cy.location('pathname').should('match', /^\/thread\//);

    cy.location('pathname').then((parentThreadPath) => {
      submitMessage('go soft');

      // The child chat runs below the switch divider, transcript kept
      cy.get('#chat-profiles').should('contain.text', 'Search');
      cy.get('.chat-boundary').should('have.length', 1);
      cy.get('.step').should('contain', 'search ready');
      cy.get('.step').should('contain', 'child chat query');
      // Wait for the app to settle on the child thread's page before
      // interacting: it navigates there on the first interaction
      cy.location('pathname')
        .should('match', /^\/thread\//)
        .and('not.eq', parentThreadPath);

      // The child thread knows its parent: the button appears
      cy.get('[data-test="open-parent-thread"]').should('exist');

      cy.get('[data-test="open-parent-thread"]').click();

      // Back in the parent thread, resumed the regular way
      cy.location('pathname').should('eq', parentThreadPath);
      cy.get('.step').should('contain', 'RESUMED');
      cy.get('#chat-profiles').should('contain.text', 'Assistant');

      // Everything stayed on screen above the return divider...
      cy.get('.chat-boundary').should('have.length', 2);
      cy.get('.step').should('contain', 'hello');
      cy.get('.step').should('contain', 'search ready');
      // ...and the replayed parent history did not duplicate anything
      cy.get('.step:contains("hello")').should('have.length', 1);
      cy.get('.step:contains("go soft")').should('have.length', 1);
      cy.get('.step:contains("child chat query")').should('have.length', 1);

      // The parent thread has no parent itself: the button is gone
      cy.get('[data-test="open-parent-thread"]').should('not.exist');

      // The input works in the resumed parent thread
      submitMessage('ping');
      cy.get('.chat-boundary')
        .last()
        .nextAll()
        .should('contain', 'profile: Assistant');
      // Still one transition: no extra dividers appeared on the round trip
      cy.get('.chat-boundary').should('have.length', 2);

      // Only the return divider carries the collapse toggle
      cy.get('[data-test="collapse-transcript"]').should('have.length', 1);
      cy.get('[data-test="collapse-transcript"]').click();

      // The child chat is folded into the strip, the parent stays visible.
      // (RESUMED can legitimately appear more than once: restoring the
      // thread's profile reconnects the session, which resumes again.)
      cy.get('[data-test="collapsed-transcript"]').should('exist');
      cy.get('.step:contains("search ready")').should('not.exist');
      cy.get('.step:contains("hello")').should('have.length', 1);
      cy.get('.step:contains("RESUMED")').should('have.length.at.least', 1);

      // New messages render below and do not reset the collapsed state
      submitMessage('while collapsed');
      cy.get('.step').should('contain', 'while collapsed');
      cy.get('[data-test="collapsed-transcript"]').should('exist');
      cy.get('.step:contains("search ready")').should('not.exist');

      // Expanding brings the child chat back
      cy.get('[data-test="collapsed-transcript"]').click();
      cy.get('.step').should('contain', 'search ready');
      cy.get('.step:contains("child chat query")').should('have.length', 1);
    });
  });

  it('still knows the parent after a reload of the child thread', () => {
    login();

    cy.get('#chat-input').should('exist');
    submitMessage('hello again');
    cy.get('.step').should('contain', 'profile: Assistant');
    cy.location('pathname').should('match', /^\/thread\//);

    cy.location('pathname').then((parentThreadPath) => {
      submitMessage('go soft');
      cy.get('#chat-profiles').should('contain.text', 'Search');
      cy.get('.step').should('contain', 'search ready');
      cy.location('pathname')
        .should('match', /^\/thread\//)
        .and('not.eq', parentThreadPath);

      // The kept transcript is client-side and dies with the reload; the
      // parent now comes from the resumed thread itself
      cy.reload();
      cy.get('#chat-input').should('exist');
      cy.get('.chat-boundary').should('not.exist');
      cy.get('.step').should('contain', 'child chat query');

      cy.get('[data-test="open-parent-thread"]').should('exist');
      cy.get('[data-test="open-parent-thread"]').click();

      cy.location('pathname').should('eq', parentThreadPath);
      // Nothing of the parent was on screen, so its history replays below
      // the single return divider
      cy.get('.chat-boundary').should('have.length', 1);
      cy.get('.step').should('contain', 'hello again');
      cy.get('.step:contains("hello again")').should('have.length', 1);
      cy.get('[data-test="collapse-transcript"]').should('have.length', 1);
    });
  });

  it('lets the app trigger the same return through open_thread', () => {
    login();

    cy.get('#chat-input').should('exist');
    submitMessage('hello api');
    cy.get('.step').should('contain', 'profile: Assistant');
    cy.location('pathname').should('match', /^\/thread\//);

    cy.location('pathname').then((parentThreadPath) => {
      submitMessage('go soft');
      cy.get('#chat-profiles').should('contain.text', 'Search');
      cy.get('.step').should('contain', 'search ready');
      // Wait for the app to settle on the child thread's page: navigating
      // there mid-typing would eat keystrokes of the trigger message
      cy.location('pathname')
        .should('match', /^\/thread\//)
        .and('not.eq', parentThreadPath);

      submitMessage('back');

      cy.location('pathname').should('eq', parentThreadPath);
      cy.get('.step').should('contain', 'RESUMED');
      cy.get('.chat-boundary').should('have.length', 2);
      cy.get('.step:contains("hello api")').should('have.length', 1);
    });
  });
});
