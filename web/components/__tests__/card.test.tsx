import { describe, expect, it } from "vitest"
import { render } from "@testing-library/react"

import { Card } from "@/components/ui/card"

describe("Card", () => {
  it("keeps its ring inside the card so clipping ancestors cannot cover it", () => {
    const { getByTestId } = render(<Card data-testid="card">content</Card>)

    expect(getByTestId("card").className).toContain("ring-inset")
  })
})
