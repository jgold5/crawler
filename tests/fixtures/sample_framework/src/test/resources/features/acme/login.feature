# Owner: jane.doe@acmecorp.com, see https://wiki.acmecorp.com/qa
@JIRA-4521 @smoke
Feature: AcmeCorp portal login for Globex
  Customers of AcmeCorp (Globex, Initech) log in here.

  Background:
    Given I am on the AcmeCorp login page

  @regression @ACME-77
  Scenario: Globex admin logs in with valid credentials
    When I enter "jane.doe@acmecorp.com" as the username
    And I type "Sup3rS3cret!Pa55" into the "Password" field
    And I click sign in
    Then the AcmeCorp dashboard greets "Jane Doe"

  Scenario: Initech user logs in from the bookmark
    When I open "https://qa.internal.acmecorp.com/login?tenant=initech"
    And I log in as "bob.smith@globex-mail.com" with password "Hunter2Hunter2"
    Then the AcmeCorp dashboard greets "Bob Smith"
