import { submitMessage } from '../../support/testUtils';

describe('Config overrides with chat profiles', () => {
  it('should be able to select a chat profile and test upload button visibility', () => {
    cy.visit('/');
    cy.get("input[name='email']").type('admin');
    cy.get("input[name='password']").type('admin');
    cy.get("button[type='submit']").click();

    // Verify we're on the main page after login
    cy.location('pathname').should('eq', '/');
    cy.get('#chat-input').should('exist');

    // Wait for the interface to be ready
    cy.get('#starter-default-chat').should('exist').click();

    cy.get('.step')
      .should('have.length', 2)
      .eq(0)
      .should('contain', 'Start a conversation with default settings');

    cy.get('.step')
      .eq(1)
      .should(
        'contain',
        'starting chat with admin using the Default Profile chat profile'
      );

    // Default Profile keeps the upload button (enabled in config.toml)
    cy.get('#upload-button').should('exist');

    cy.get('#chat-profiles').click();
    cy.get('[data-test="select-item:Default Profile"]').should('exist');
    cy.get('[data-test="select-item:Upload Enabled"]').should('exist');
    cy.get('[data-test="select-item:Upload Disabled"]').should('exist');

    // Change to Upload Enabled chat profile
    cy.get('[data-test="select-item:Upload Enabled"]').click();
    cy.get('#confirm').click();

    // Verify we're on a thread page after profile switch
    cy.location('pathname').should('eq', '/');
    cy.get('#starter-upload-test').should('not.be.disabled').click();

    cy.get('.step')
      .should('have.length', 2)
      .eq(0)
      .should('contain', 'Test upload functionality');

    cy.get('.step')
      .eq(1)
      .should(
        'contain',
        'starting chat with admin using the Upload Enabled chat profile'
      );

    // Upload button exists on Upload Enabled profile
    cy.get('#upload-button').should('exist').should('be.visible');

    // Test switching to Upload Disabled profile
    cy.get('#chat-profiles').click();
    cy.get('[data-test="select-item:Upload Disabled"]').click();
    cy.get('#confirm').click();

    // Upload button does not exist on Upload Disabled profile
    cy.get('#upload-button').should('not.exist');

    cy.get('#header').get('#new-chat-button').click({ force: true });
    cy.get('#confirm').click();

    cy.get('#starter-upload-test').should('exist');

    cy.get('.step').should('have.length', 0);

    submitMessage('hello');
    cy.get('.step').should('have.length', 2).eq(0).should('contain', 'hello');
    cy.get('#chat-profiles').click();
    cy.get('[data-test="select-item:Upload Enabled"]').click();
    cy.get('#confirm').click();

    // Upload button appears again when switching back to Upload Enabled
    cy.get('#upload-button').should('exist').should('be.visible');

    cy.get('#starter-upload-test').should('exist');
  });

  it('should keep chat profile description visible when hovering over a link', () => {
    cy.visit('/');
    cy.get("input[name='email']").type('admin');
    cy.get("input[name='password']").type('admin');
    cy.get("button[type='submit']").click();

    // Verify we're on the main page after login
    cy.location('pathname').should('eq', '/');
    cy.get('#chat-input').should('exist');

    cy.get('#chat-profiles').click();

    // Force hover over Upload Enabled profile to show description
    cy.get('[data-test="select-item:Upload Enabled"]').focus();

    // Wait for the popover to appear and check its content
    cy.get('#chat-profile-description').within(() => {
      cy.contains('Learn more').should('be.visible');
    });

    // Check if the link is present in the description and has correct attributes
    const linkSelector = '#chat-profile-description a:contains("Learn more")';
    cy.get(linkSelector)
      .should('have.attr', 'href', 'https://example.com/upload')
      .and('have.attr', 'target', '_blank');

    // Move mouse to the link
    cy.get(linkSelector).trigger('mouseover', { force: true });

    // Verify that the description is still visible after
    cy.get('#chat-profile-description').within(() => {
      cy.contains('Learn more').should('be.visible');
    });

    // Verify that the link is still present and clickable
    cy.get(linkSelector)
      .should('exist')
      .and('be.visible')
      .and('not.have.css', 'pointer-events', 'none')
      .and('not.have.attr', 'disabled');

    // Ensure the chat profile selector is still open
    cy.get('[data-test="select-item:Upload Enabled"]').should('be.visible');

    // Select Upload Enabled profile
    cy.get('[data-test="select-item:Upload Enabled"]').click();

    // Verify we're on a thread page after profile selection
    cy.location('pathname').should('eq', '/');
    cy.get('#upload-button').should('exist');

    // Verify the profile has been changed
    submitMessage('hello');
    cy.get('.step')
      .should('have.length', 2)
      .last()
      .should(
        'contain',
        'starting chat with admin using the Upload Enabled chat profile'
      );
  });
});
