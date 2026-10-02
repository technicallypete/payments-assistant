# AI Software Engineer Take-Home Assessment: The AI Payments Assistant

As the next step in our process, this take-home assessment is designed to give you an opportunity to showcase your skills on a practical, real-world problem. Our goal is to see how you approach building a product that combines software engineering best practices with modern AI capabilities.

You are encouraged to use modern AI-powered development tools (e.g., Claude, Cursor, v0.dev) to assist you. The goal is to see how you leverage these tools to build effectively. However, **we do not accept solutions built on no-code/low-code platforms like Zapier or n8n**. The final submission must be your own code.

We respect your time and have designed this test to be completed within **3–5 hours**. Please don't feel the need to over-engineer your solution; we are most interested in your thought process, code quality, and ability to deliver a functional proof-of-concept. The bonus section is entirely optional.

Good luck, and we look forward to seeing what you build!

---

## Getting Started

1. **Set Up Your Project**  
   Start a fresh project however you like. Use `git` from the beginning and commit your changes regularly, so we can see how the work came together.

2. **Create a Free Stripe Account**  
   Sign up at [stripe.com](https://stripe.com) and work entirely in **test mode / sandbox**. No credit card is required and no real money will move. Stripe publishes [test cards](https://docs.stripe.com/testing) you can use to simulate successful payments, declines, and other outcomes.

---

## 1. The Challenge: Build an AI Payments Assistant

Your task is to build an AI-powered assistant that helps a small business owner manage the money flowing through their Stripe account.

### Part 1: The Business Payments Assistant (Web App)

A web app with a chatbot-style interface where the business owner can securely review and manage payments, customers, and invoices.

#### Core Requirements

**Backend Server:**
- Build a simple backend server using the language and framework of your choice (e.g., Ruby on Rails/Python/FastAPI, Node.js/Express, Go).
- Integrate with Stripe to read payment activity and to create charges, invoices, refunds, or payment links.
- Integrate with an LLM of your choice (e.g., via OpenAI, Google AI, Anthropic, or a local model).
- Provide core assistant logic via an API for the frontend. Document your API design choices in your `README.md`.

**Required Features:**
- **Daily Summary Generation:** Fetch the day's payment activity and use the LLM to generate a concise, human-like summary. A simple list of transactions is not enough. For example:
  > "You took $4,280 across 18 payments today, well ahead of yesterday. Two cards were declined for insufficient funds, and there's still an unpaid $1,200 invoice sitting with Acme Corp."
- **Natural Language Commands:** Process instructions like:
  > "Refund Maya's last payment"  
  > "Create a $250 invoice for Acme Corp due next Friday"  
  > "How much did we take last week compared to the week before?"

- Extract intent, amounts, customers, dates, and any other details you need, then carry out the corresponding action in Stripe.

- Feel free to create your own unique AI personality.

**Frontend Interface:**
- Create a single-page web application.
- Include a text input for commands (e.g., "summarize my day", "refund the last charge from...") and a display area for assistant responses.
- This is your chance to get creative, surprise us with your design!

---

### Part 2: The Customer Payment Bot (Telegram Bot)

A Telegram bot that allows **external customers** to pay what they owe without seeing anything about the business's other customers or overall finances.

#### Core Requirements

**Telegram Bot Integration:**
- Create a simple Telegram bot that can respond to user messages.

**Privacy-Preserving Logic:**
- A customer may only see and act on **their own** invoices and payments.
- Never reveal other customers, total revenue, or any account-level financial information.
- Payments of **$2,000 or more** must not be completed by the bot. Hand those off to the business owner instead.

**Payment Workflow:**
- Allow an external customer to message the bot, find out what they owe, and pay it.

---

### Part 3: Bonus Feature (Optional)

If you have time and inspiration, add one feature you believe would improve the product. This is your chance to demonstrate **creativity and product sense**. In your `write-up.md`, explain why you chose this feature and how it adds value.

---

## 2. Deliverables

Send us your project as either a **private repository** (invite us as collaborators) or a **zip archive** with the `.git` directory included and `node_modules` or equivalent excluded. Either way it should contain the following:

- **Source Code:** All backend and frontend code for all parts of the project.
- **A seed script:** A single command that populates a fresh Stripe test account with the sample customers, invoices, and payment activity your app expects. A new Stripe sandbox starts empty, and we will run your seed script against our own test account before we run your app.
- **README.md:**
  - Brief overview of your architectural choices and why you made them.
  - Clear, step-by-step setup instructions (including installing dependencies and configuring environment variables such as Stripe API keys, LLM API key, Telegram Bot token).
  - Instructions on how to run and test all parts of the application, including how to run your seed script. Assume we will run everything against **our own Stripe test account**, not yours, so nothing should depend on data you created by hand in your dashboard.
- **write-up.md:**
  - Any assumptions you made.
  - Challenges you faced and how you overcame them.
  - Limitations of your current solution and how you would improve it with more time.
  - If you did the bonus, a section explaining your chosen feature.

---

## 3. FAQ

**1. What is the policy about using AI in this test?**  
AI usage is encouraged! This is an *"open book"* assessment. Feel free to use the same tools you'd rely on in a real job, including Google, StackOverflow, and AI assistants.

**2. Are there any specific requirements for the API implementation, such as response time constraints or throughput targets?**  
We're more interested in your improvement process and thought process than in hitting specific performance metrics.

**3. Should I consider common security issues like DoS or other attacks?**  
No need to worry about infrastructure-level attacks. The focus is on the software itself.

**4. Will I be charged for any of this?**  
No. Stripe is free in test mode and no real money moves. For the LLM, there are plenty of free options available online. If you can't find one, just request an API key from me:

---

If you have any questions, please reach out to me: **jzupancic@replicant.ai**
