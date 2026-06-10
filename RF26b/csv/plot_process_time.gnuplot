set terminal pdfcairo enhanced color font ',10'
set datafile separator comma
set key outside top center horizontal
set grid ytics
set xlabel 'MPI ranks'
set ylabel 'Mean process time [s]'
set logscale x 2
set output 'plots/rf26b_process_time.pdf'
plot for [rate in '0.000002 0.000006'] \
     for [m in '1 2 3 4 5 6'] \
     'rf26b_scaling.csv' using \
     (strcol(3) eq rate && strcol(4) eq m && strcol(1) eq 'ok' ? $6 : 1/0):9 \
     with linespoints title sprintf('s=%s, m=%s', rate, m)
