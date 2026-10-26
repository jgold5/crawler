@orders @JIRA-4600
Feature: Orders grid for the AcmeCorp loan book

  Rule: Editing quantities

    Scenario Outline: Update order for <customer>
      Given I log in as "<user>" with password "<password>"
      When I set "quantity" to "<qty>" in row "<orderId>" of the orders grid
      And I open order "<orderId>"
      Then the order total shows 3

      Examples:
        | customer | user                      | password       | qty | orderId |
        | Globex   | bob.smith@globex-mail.com | Hunter2Hunter2 | 5   | A-100   |

    Scenario: Overdraft settings for Initech
      When I choose "Wire transfer" from the transfer type dropdown
      And I tick the approve checkbox
      And I hover over the transfer amount
      And the API seeds 25 orders for Initech
      And customer Globex has these contacts:
        | name     | email                 |
        | Jane Doe | jane.doe@acmecorp.com |
      And the import payload is:
        """json
        {"customer": "Globex", "ssn": "123-45-6789", "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJxYS1ib3QifQ.c2lnbmF0dXJlMTIz"}
        """
