package pe.axiz.acquirer;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.util.*;
import org.junit.jupiter.api.Test;
import pe.axiz.acquirer.application.ports.out.AuthorizationStore;
import pe.axiz.acquirer.application.service.AuthorizationService;
import pe.axiz.acquirer.domain.Authorization;

class AuthorizationServiceTest {
  @Test
  void delegatesStablePaymentId() {
    var s = mock(AuthorizationStore.class);
    var a =
        new Authorization(UUID.randomUUID(), "merchant-demo", BigDecimal.ONE, "PEN", "tok_demo");
    var d = new AuthorizationStore.Decision("AUTHORIZED", "AX1");
    when(s.reserve(a, false)).thenReturn(d);
    assertThat(new AuthorizationService(s).authorize(a, false)).isEqualTo(d);
  }

  @Test
  void absentAuthorization() {
    var s = mock(AuthorizationStore.class);
    when(s.find(any())).thenReturn(Optional.empty());
    assertThatThrownBy(() -> new AuthorizationService(s).get(UUID.randomUUID()))
        .isInstanceOf(NoSuchElementException.class);
  }
}
