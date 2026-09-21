describe('Starters with Categories', () => {
  it('shows every category as a section, in the server’s order', () => {
    cy.get('#starters').should('exist');

    cy.get('[data-test^="starter-category-"]').should('have.length', 3);
    cy.get('[data-test^="starter-category-"]')
      .eq(0)
      .should('have.attr', 'data-test', 'starter-category-Creative');
    cy.get('[data-test^="starter-category-"]')
      .eq(1)
      .should('have.attr', 'data-test', 'starter-category-Educational');
    cy.get('[data-test^="starter-category-"]')
      .eq(2)
      .should('have.attr', 'data-test', 'starter-category-Errands');
  });

  it('shows the starters of every section with nothing pressed', () => {
    // The categories used to be pills that revealed their starters on a
    // click. There is nothing to click now: the whole offer is readable on
    // arrival.
    cy.get('#starter-poem').should('be.visible');
    cy.get('#starter-story').should('be.visible');
    cy.get('#starter-explain').should('be.visible');
  });

  it('carries a section description', () => {
    cy.contains('the same as typing, but with a button').should('be.visible');
  });

  it('gives a plate its description and its caption', () => {
    cy.get('#starter-explain').contains('a walk through it, step by step');
    cy.get('#starter-explain').contains('~2 min');
  });

  it('shows a disabled starter without letting it be used', () => {
    cy.get('#starter-subscribe').should('be.visible');
    cy.get('#starter-subscribe').should('be.disabled');
    cy.get('#starter-subscribe').should('have.attr', 'aria-disabled', 'true');
    cy.get('.step').should('have.length', 0);
  });

  it('folds a collapsible section behind a summary that counts it', () => {
    cy.get('[data-test="starter-category-Errands"]').should('match', 'details');
    // The count answers "how much is folded away", so it is drawn where a
    // section arrives folded — on a phone. At the desk the section is open
    // and a count of what is already on screen is noise beside the heading.
    cy.viewport('iphone-x');
    cy.get('[data-test="starter-category-Errands"] summary')
      .contains('· 1')
      .should('be.visible');
  });

  it('should be able to use a starter from a category', () => {
    cy.get('#starter-poem').should('exist').click();
    cy.get('.step').should('have.length', 2);
    cy.get('.step').eq(0).contains('Write a poem');
  });
});
