// Registers jest-dom's matchers (toBeInTheDocument and friends) with vitest's
// expect, and unmounts rendered trees between tests. Auto-cleanup is explicit
// here because these tests import from 'vitest' rather than relying on globals.
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'

afterEach(cleanup)
